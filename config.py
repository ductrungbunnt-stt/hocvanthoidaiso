import os
from dotenv import load_dotenv

load_dotenv()
class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "KBA4EiD9flE6Tim2DzpgJPNhY")
    MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://tranxuancong2023:qUJomTP0jDdHmuNV@cluster0.sfcxjee.mongodb.net/ml?retryWrites=true&w=majority")

config = Config()
