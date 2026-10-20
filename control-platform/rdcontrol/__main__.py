import argparse
import os
from aiohttp import web
from .app import make_app

parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", required=True)
parser.add_argument("--public-url", required=True)
parser.add_argument("--port", type=int, default=8088)
args = parser.parse_args()
os.umask(0o077)
app = make_app(args.data_dir, args.public_url)
web.run_app(app, host="127.0.0.1", port=args.port, access_log=None)
