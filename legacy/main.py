import os
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from agent_controller import AgentController

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

agent_controller = AgentController()

API_KEY = os.getenv("API_KEY")


class ChatRequest(BaseModel):
    messages: list[dict]


def verify_api_key(authorization: str | None):
    if not API_KEY:
        return
    expected = f"Bearer {API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


@app.post("/chat")
def chat(request: ChatRequest, authorization: str | None = Header(default=None)):
    verify_api_key(authorization)
    response = agent_controller.get_response({"input": {"messages": request.messages}})
    return {"output": response}


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
