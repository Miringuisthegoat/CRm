from flask import Flask
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from app.api.routes import api_bp, web_bp
from app.auth.routes import auth_bp
from app.core import socket_events  # noqa: F401
from app.extensions import csrf, db, limiter, login_manager, migrate, socketio


def create_app(config_object=Config):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    app.config.from_object(config_object)

    if app.config["SECRET_KEY"] == "dev-secret" and not app.debug:
        raise RuntimeError("Set a real SECRET_KEY in .env before running in production.")

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    socketio.init_app(app, async_mode="eventlet")
    login_manager.login_view = "auth.login"

    app.register_blueprint(web_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)

    return app