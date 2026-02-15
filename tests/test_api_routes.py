from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"

def test_assignment_flow():
    # 1. Create Assignment
    payload = {
        "teacher": "teacher1",
        "student": "student1",
        "question": "What is 2+2?"
    }
    response = client.post("/api/v1/assignments/create", json=payload)
    assert response.status_code == 200
    aid = response.json()["id"]
    
    # 2. Student Fetches It
    res_get = client.get("/api/v1/assignments/student/student1")
    assert len(res_get.json()) > 0
    assert res_get.json()[0]['id'] == aid