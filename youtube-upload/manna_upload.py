#!/usr/bin/env python3
"""Manna Minis pipeline: YouTube upload step (complete, standalone; Python 3.9+ standard library only).

This is the upload step the Manna Minis pipeline runs after the channel owner has watched and approved a finished
quiz video. It only ever uploads to the owner's own channel, always as PRIVATE and Made for Kids; the owner
publishes by hand in YouTube Studio. It never makes a video public.

Commands:
  python3 manna_upload.py auth --client client_secret.json
      One-time Google sign-in (browser, loopback + PKCE). Saves the refresh token to manna_upload.env
      (keep that file private). Scopes: youtube.upload and youtube.readonly.

  python3 manna_upload.py upload --video quiz.mp4 --thumbnail thumb.png --metadata metadata.json
      Uploads the video with videos.insert (part=snippet,status; privacyStatus=private,
      selfDeclaredMadeForKids=true; resumable upload), then sets the thumbnail with thumbnails.set.
      metadata.json: {"title": ..., "description": ..., "tags": [...], "language": "en"}
      (--title/--description/--tags/--language can be given instead of, or on top of, the file.)

  python3 manna_upload.py status VIDEO_ID
      Reads the video's privacy and upload status with videos.list (part=status). The pipeline runs this
      once a day to see whether the owner has published the video.

YouTube Data API calls used: videos.insert, thumbnails.set, videos.list. Nothing else.
"""
import argparse, base64, hashlib, http.server, json, mimetypes, os, secrets, sys, time
import urllib.error, urllib.parse, urllib.request, webbrowser
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
ENV_FILE = Path(__file__).with_name("manna_upload.env")
EDUCATION = "27"
CHUNK = 8 * 1024 * 1024


class Fail(Exception):
    pass


# ---------- credentials ----------

def load_env():
    vals = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() and v.strip():
                vals[k.strip()] = v.strip()
    for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        if os.environ.get(k):
            vals[k] = os.environ[k]
    return vals


def access_token():
    env = load_env()
    missing = [k for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN") if not env.get(k)]
    if missing:
        raise Fail(f"Missing {', '.join(missing)}. Run: python3 manna_upload.py auth --client client_secret.json")
    body = urllib.parse.urlencode({"client_id": env["GOOGLE_CLIENT_ID"], "client_secret": env["GOOGLE_CLIENT_SECRET"],
                                   "refresh_token": env["GOOGLE_REFRESH_TOKEN"], "grant_type": "refresh_token"}).encode()
    try:
        return json.loads(urllib.request.urlopen(TOKEN_URL, body, timeout=30).read())["access_token"]
    except urllib.error.HTTPError as e:
        raise Fail(f"Google sign-in failed ({e.code}); run the auth command again.") from None


def auth(client_path):
    client = json.loads(Path(client_path).read_text(encoding="utf-8"))
    client = client.get("installed") or client.get("web") or client
    cid, csecret = client["client_id"], client["client_secret"]
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state, got = secrets.token_urlsafe(16), {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<p style='font:20px sans-serif'>Done. You can close this tab.</p>")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})
    print("Sign in with the Google account that owns the YouTube channel:\n" + url)
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        server.handle_request()
    if "error" in got:
        raise Fail(f"Sign-in error: {got['error']}")
    body = urllib.parse.urlencode({"code": got["code"], "client_id": cid, "client_secret": csecret, "redirect_uri": redirect,
                                   "grant_type": "authorization_code", "code_verifier": verifier}).encode()
    tok = json.loads(urllib.request.urlopen(TOKEN_URL, body, timeout=30).read())
    if not tok.get("refresh_token"):
        raise Fail("No refresh token returned. Remove the app at https://myaccount.google.com/permissions and try again.")
    ENV_FILE.write_text(f"GOOGLE_CLIENT_ID={cid}\nGOOGLE_CLIENT_SECRET={csecret}\nGOOGLE_REFRESH_TOKEN={tok['refresh_token']}\n", encoding="utf-8")
    os.chmod(ENV_FILE, 0o600)
    print(f"Saved the sign-in to {ENV_FILE.name} (keep it private; never commit it).")


# ---------- API calls ----------

def api(method, url, token, body=None, params=None, headers=None, retries=4):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    h = {"Authorization": f"Bearer {token}", **(headers or {})}
    if isinstance(body, dict):
        h["Content-Type"] = "application/json; charset=utf-8"
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=h), timeout=120) as r:
                out = r.read()
                return json.loads(out) if out else {}
        except urllib.error.HTTPError as e:
            if e.code in (500, 502, 503, 504) and attempt < retries:
                time.sleep(5 * 2 ** attempt)
                continue
            raise Fail(f"{method} {url.split('?')[0]} -> {e.code}: {e.read().decode('utf-8', 'replace')[:600]}") from None


def resumable_upload(token, video, resource):
    """videos.insert with the resumable protocol: start a session, then send 8 MB chunks, resuming after errors."""
    size = video.stat().st_size
    start = urllib.request.Request(f"{UPLOAD_API}/videos?uploadType=resumable&part=snippet,status",
                                   data=json.dumps(resource).encode(), method="POST", headers={
                                       "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8",
                                       "X-Upload-Content-Type": mimetypes.guess_type(video.name)[0] or "video/mp4",
                                       "X-Upload-Content-Length": str(size)})
    try:
        session = urllib.request.urlopen(start, timeout=60).headers["Location"]
    except urllib.error.HTTPError as e:
        raise Fail(f"videos.insert -> {e.code}: {e.read().decode('utf-8', 'replace')[:600]}") from None
    sent, errors = 0, 0
    with video.open("rb") as f:
        while True:
            f.seek(sent)
            part = f.read(CHUNK)
            rng = f"bytes {sent}-{sent + len(part) - 1}/{size}" if part else f"bytes */{size}"
            req = urllib.request.Request(session, data=part, method="PUT", headers={"Content-Length": str(len(part)), "Content-Range": rng})
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    print(f"  uploaded {size / 1e6:.1f} MB")
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code == 308:  # chunk received; continue from the confirmed byte
                    got = e.headers.get("Range")
                    sent = int(got.split("-")[1]) + 1 if got else 0
                    print(f"  {sent / size:5.1%}", end="\r", flush=True)
                    continue
                if e.code in (500, 502, 503, 504) and errors < 8:
                    errors += 1
                    time.sleep(min(60, 2 ** errors))
                    sent = -1  # ask the server how much it has
                    status = urllib.request.Request(session, data=b"", method="PUT", headers={"Content-Length": "0", "Content-Range": f"bytes */{size}"})
                    try:
                        urllib.request.urlopen(status, timeout=60)
                    except urllib.error.HTTPError as s:
                        got = s.headers.get("Range") if s.code == 308 else None
                        sent = int(got.split("-")[1]) + 1 if got else 0
                    continue
                raise Fail(f"upload -> {e.code}: {e.read().decode('utf-8', 'replace')[:600]}") from None


def upload(args):
    video, thumb = Path(args.video), Path(args.thumbnail) if args.thumbnail else None
    if not video.is_file():
        raise Fail(f"No video file at {video}")
    if thumb and not thumb.is_file():
        raise Fail(f"No thumbnail file at {thumb}")
    meta = json.loads(Path(args.metadata).read_text(encoding="utf-8")) if args.metadata else {}
    title = args.title or meta.get("title")
    description = args.description or meta.get("description", "")
    tags = args.tags.split(",") if args.tags else meta.get("tags", [])
    meta_lang = meta.get("language")  # the pipeline writes {"code": "en", "name": "English"}
    lang = args.language or (meta_lang.get("code") if isinstance(meta_lang, dict) else meta_lang) or "en"
    if not title:
        raise Fail("A title is required (--title or metadata.json).")
    resource = {
        "snippet": {"title": title[:100], "description": description[:5000], "tags": [t.strip() for t in tags if t.strip()],
                    "categoryId": EDUCATION, "defaultLanguage": lang, "defaultAudioLanguage": lang},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": True, "embeddable": True},
    }
    token = access_token()
    print(f"Uploading {video.name} as PRIVATE, Made for Kids: {title}")
    res = resumable_upload(token, video, resource)
    vid = res["id"]
    print(f"Video id {vid}, privacy {res['status']['privacyStatus']}: https://www.youtube.com/watch?v={vid}")
    if thumb:
        api("POST", f"{UPLOAD_API}/thumbnails/set", token, body=thumb.read_bytes(), params={"videoId": vid, "uploadType": "media"},
            headers={"Content-Type": mimetypes.guess_type(thumb.name)[0] or "image/png"})
        print("Thumbnail set.")
    print("Next: the channel owner reviews it in YouTube Studio and presses Publish.")
    return vid


def status(video_id):
    items = api("GET", f"{API}/videos", access_token(), params={"part": "status", "id": video_id})["items"]
    if not items:
        raise Fail(f"No video {video_id} on this channel (deleted, or still processing).")
    s = items[0]["status"]
    print(json.dumps({"id": video_id, "privacyStatus": s["privacyStatus"], "uploadStatus": s["uploadStatus"],
                      "madeForKids": s.get("madeForKids")}, indent=2))
    return s["privacyStatus"]


def main():
    p = argparse.ArgumentParser(description="Manna Minis pipeline: upload a quiz video to the owner's channel as private.")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("auth", help="one-time Google sign-in")
    a.add_argument("--client", required=True, help="OAuth client JSON (Desktop app) from Google Cloud")
    u = sub.add_parser("upload", help="upload a video as private with its thumbnail")
    u.add_argument("--video", required=True)
    u.add_argument("--thumbnail")
    u.add_argument("--metadata", help="metadata.json with title, description, tags, language")
    u.add_argument("--title")
    u.add_argument("--description")
    u.add_argument("--tags", help="comma-separated")
    u.add_argument("--language")
    s = sub.add_parser("status", help="privacy status of an uploaded video")
    s.add_argument("video_id")
    args = p.parse_args()
    try:
        if args.cmd == "auth":
            auth(args.client)
        elif args.cmd == "upload":
            upload(args)
        else:
            status(args.video_id)
    except Fail as e:
        sys.exit(f"ERROR: {e}")


if __name__ == "__main__":
    main()
