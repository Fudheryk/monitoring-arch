from flask import Flask, request
app = Flask(__name__)
state = {"code": 503}

@app.get("/")
def health():
    return ("", state["code"])

@app.post("/set")
def set_code():
    code = int(request.args.get("code", "200"))
    code = max(100, min(599, code))
    state["code"] = code
    return {"ok": True, "code": state["code"]}, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=80)
