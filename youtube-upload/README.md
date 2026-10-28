# Manna Minis pipeline: YouTube upload step

`manna_upload.py` is the complete upload step of the Manna Minis pipeline, as one standalone script
(Python 3.9+, standard library only). The pipeline runs it after the channel owner has watched and approved a
finished quiz video. See [How it works](https://lemuelsinghr.github.io/manna-minis-site/how-it-works.html).

## What it does

| Command | YouTube Data API call | Purpose |
|---|---|---|
| `upload` | `videos.insert` (part=snippet,status, resumable) | Uploads the approved video to the owner's own channel as **private**, `selfDeclaredMadeForKids: true`, category Education, with title, description, tags and language |
| `upload` | `thumbnails.set` | Sets the video's custom thumbnail |
| `status` | `videos.list` (part=status) | Reads the video's privacy status, so the pipeline knows when the owner has published it |

It never makes a video public: the owner reviews the private upload in YouTube Studio and presses Publish.
It does not use search, comments, analytics or any other channel.

## Run it

1. In Google Cloud, create an OAuth client of type **Desktop app** and download its JSON as `client_secret.json`.
2. Sign in once with the Google account that owns the channel (opens a browser):

   ```
   python3 manna_upload.py auth --client client_secret.json
   ```

3. Upload a finished video as private, with its thumbnail and metadata:

   ```
   python3 manna_upload.py upload --video gen-04-video-en.mp4 --thumbnail gen-04-thumbnail.png --metadata metadata.json
   ```

   `metadata.json` (written by the pipeline's packaging step):

   ```json
   {"title": "Manna Minis Genesis Chapter 4 Bible Quiz for Kids",
    "description": "A fun Bible quiz for kids with 10 questions about Genesis chapter 4. ...",
    "tags": ["Bible quiz for kids", "Genesis", "Manna Minis"],
    "language": {"code": "en", "name": "English"}}
   ```

4. Check whether the owner has published it:

   ```
   python3 manna_upload.py status VIDEO_ID
   ```

The sign-in is saved in `manna_upload.env` next to the script (git-ignored; keep it private).
