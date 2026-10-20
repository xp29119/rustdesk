import argparse
import os
from aiohttp import web
from .app import make_app

parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", required=True)
parser.add_argument("--public-url", required=True)
parser.add_argument("--admin-origin")
parser.add_argument("--admin-cookie-path", default="/")
parser.add_argument("--admin-cookie-name", default="control_session")
parser.add_argument("--port", type=int, default=8088)
args = parser.parse_args()
os.umask(0o077)
app = make_app(args.data_dir, args.public_url, admin_origin=args.admin_origin,
               admin_cookie_path=args.admin_cookie_path, admin_cookie_name=args.admin_cookie_name)
web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)
