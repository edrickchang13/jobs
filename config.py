import os
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GITHUB_REPO_URL = os.getenv(
    "GITHUB_REPO_URL",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md",
)
CHROME_PROFILE_DIR = os.getenv("CHROME_PROFILE_DIR", "")
SIMPLIFY_EXTENSION_PATH = os.getenv("SIMPLIFY_EXTENSION_PATH", "")
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))
AUTO_SUBMIT = os.getenv("AUTO_SUBMIT", "false").lower() == "true"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DB_PATH = "auto_apply.db"
RESUMES_DIR = "resumes"
SCREENSHOTS_DIR = "screenshots"

# Candidate profile - used by LLM to generate resumes
CANDIDATE_PROFILE = """
Name: Edrick Chang
Phone: (408) 806-6495
Email: eachang@scu.edu
LinkedIn: linkedin.com/in/edrickchang

Education:
- Santa Clara University, B.S. Computer Science & Engineering, GPA: 3.78/4.0 (Expected June 2028), Santa Clara, CA
- Bellarmine College Preparatory, High School Diploma, GPA: 3.9/4.0 Unweighted (Graduated 2024), San Jose, CA

Relevant Coursework:
CSEN 11 Intro to Programming Concepts, CSEN 12 Data Structures and Algorithms, CSEN 19 Discrete Math,
CSEN 79 Object Oriented Programming, CSEN 20 Embedded Systems, ECEN 21 Logic & Design

Experience:
- Real-Time AI Lawyer (Power2ThePeople), Feb 2026, NVIDIA Spark Hackathon, 1st Place / 75 Teams, Santa Clara, CA
  - Developed a human-centered, real-time AI system assisting users during high-stress legal encounters using live video and audio input
  - Designed perception and reasoning pipelines to evaluate legality of interactions and detect missed procedural safeguards
  - Applied retrieval-augmented generation (RAG) over public misconduct datasets to contextualize encounters with similar real-world cases
  - Optimized low-latency inference workflows on NVIDIA DGX Spark for real-time decision support

- Agentic System for Incident Response (DevAngel), Oct 2025, AWS x INRIX Hackathon, 2nd Place / 40 Teams, San Jose, CA
  - Built an intelligent decision-support system to assist engineers in diagnosing and responding to complex system failures in real time
  - Designed visual analytics to surface temporal patterns, system states, and impacted components during incidents
  - Implemented automated data pipelines using AWS Lambda, Step Functions, and CloudWatch Logs Insights
  - Prioritized clarity, reduced cognitive load, and rapid interpretation under time pressure
  - Deployed scalable, serverless infrastructure with continuous integration and live updates

- AI-Powered STEM Tutoring Platform (Equity.edu), Mar 2026, Hack for Humanity 2026, Santa Clara, CA
  - Developed an accessible AI-powered STEM tutoring platform enabling students to upload written work and receive contextual feedback and guided hints
  - Designed real-time workspace awareness using a tldraw canvas with voice-enabled interaction and Socratic guidance
  - Built backend reasoning pipelines combining vision-language models (vLLM) with fallback generative services to interpret handwritten input

- AI Calorie Tracker (MealSense), Feb 2025, Hack for Humanity 2025, Santa Clara, CA
  - Developed a personalized nutrition assistance system aligned with user health goals and institutional dining constraints
  - Modeled food intake data and macronutrient profiles to support behavior-aware recommendations
  - Built mobile interface using React Native with backend services in FastAPI and Firebase
  - Designed system extensibility for varying environments and diverse user needs

Technical Skills:
- Programming: C++, Python, C, Java, MATLAB (familiar), Swift
- Systems & Tools: ROS2, Git, AWS, CI/CD, Serverless Architectures
- Human-Centered Systems: Real-Time Inference, Assistive AI, Decision Support Systems
- Data & Research: Data Pipelines, Logging & Metrics Analysis, Dataset Curation, Experimental Iteration
- Design Concepts: Human Factors, Cognitive Load Reduction, Robust Systems in High-Stress Environments
"""

# Writing style constraints
WRITING_STYLE = """
CRITICAL STYLE RULES:
- Do NOT use em dashes anywhere
- Do NOT use colons at the start of sentences
- Do NOT use semicolons
- Sound human and authentic, not AI-generated
- Keep answers concise (under 200 words unless the field suggests otherwise)
"""
