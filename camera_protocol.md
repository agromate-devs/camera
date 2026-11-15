Topics are reserved to a single webcam (`<id>` is the webcam username, e.g. `cam_xxxxxxxx`) and only the webcam and its owner can access them:
- `webrtc/<id>/signaling`: main topic
- `webrtc/<id>/<viewerId>`: topic reserved for a viewer

- Viewer send a register-view message on the main topic where it announce the topic reserved for communication between it and broadcast server
- Broadcaster send a viewer-registered message to signal that is ready to listen on reserved topic
- Viewer send offer
- Viewer send ICE candidates
- Broadcaster send answer to start WebRTC connection

Coturn needs a username/password for broadcaster: the webcam gets them from `GET /turn` using HTTP Basic authentication with its own username/password.
