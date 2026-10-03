from app.extensions import socketio


@socketio.on("connect")
def handle_connect():
    socketio.emit("system", {"message": "Connected to Carace"})
