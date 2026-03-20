# PhotoTVHDRViewer

## Deployment

- Pi 5 SSH host: `pi5` (configured in ~/.ssh/config)
- Remote app directory: `/home/haro/photo-hdr`
- Deploy with: `rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.DS_Store' pi-hdr-viewer/ pi5:/home/haro/photo-hdr/pi-hdr-viewer/`

## Running on Pi

- User `haro` is in `video` and `input` groups — no sudo needed
- Run: `python3 -u /home/haro/photo-hdr/pi-hdr-viewer/main.py [photo_dir]`
- Use `-u` flag for unbuffered output when redirecting to a log file
- Quit via pipe: `echo q > /tmp/hdr-viewer-cmd`
