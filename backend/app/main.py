# backend/app/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # <-- Import this
from app.api import endpoints

app = FastAPI(
    title="AI Tutor API",
    description="API for the multi-agent AI Tutor system.",
    version="1.0.0"
)

# --- Add CORS Middleware ---
# This is the new, important section.
# It allows your frontend (e.g., running on localhost:3000)
# to communicate with your backend (running on localhost:8000).

# In production, you would want to restrict this to your actual frontend domain
# for security. For development, allowing all origins is common.
origins = [
    "http://localhost",
    "http://localhost:3000",  # Default for React
    "http://localhost:5173",  # Default for Vite/React
    # Add the URL of your deployed frontend here when you deploy
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allows all headers
)
# --- End of CORS Middleware Section ---


# Include the API router
app.include_router(endpoints.router, prefix="/api/v1")

@app.get("/", tags=["Root"])
def read_root():
    return {"message": "Welcome to the AI Tutor API!"}