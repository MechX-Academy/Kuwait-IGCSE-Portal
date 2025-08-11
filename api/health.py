from flask import Flask, jsonify
app = Flask(__name__)

@app.route("/", defaults={"subpath": ""}, methods=["GET"])
@app.route("/<path:subpath>", methods=["GET"])
def health(subpath=None):
    return jsonify(ok=True, msg="Running")
