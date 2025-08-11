from flask import Flask, jsonify
app = Flask(__name__)

@app.get("/")
def health():
    return jsonify(ok=True, msg="Running")
