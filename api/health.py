from flask import Flask, jsonify
app = Flask(__name__)

# accept "/" أو أي subpath (لتفادي اختلاف المسارات على Vercel)
@app.route("/", defaults={"subpath": ""}, methods=["GET"])
@app.route("/<path:subpath>", methods=["GET"])
def health(subpath):
    return jsonify(ok=True, msg="Running")
