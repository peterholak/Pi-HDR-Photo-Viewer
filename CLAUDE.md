# PhotoTVHDRViewer

## Deployment

- Pi 5 SSH host: `pi5` (configured in ~/.ssh/config)
- Remote app directory: `/home/haro/photo-hdr`
- Deploy with: `rsync -avz --exclude='.venv' --exclude='__pycache__' --exclude='.DS_Store' pi-hdr-viewer/ pi5:/home/haro/photo-hdr/pi-hdr-viewer/`

## Running on Pi

- Requires root (or `input` group) for DRM/KMS and evdev access
- Run: `sudo python3 /home/haro/photo-hdr/pi-hdr-viewer/main.py [photo_dir]`
