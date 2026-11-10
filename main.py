import os
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from google_auth_oauthlib.flow import Flow
from dotenv import load_dotenv
from sqlalchemy.future import select
from database import init_db, async_session, User

load_dotenv()
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

app = FastAPI(title="AI Expense Tracker Auth Server")

CLIENT_SECRETS = {
    "web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")]
    }
}

SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets'
]

# Временная память для хранения защитных кодов PKCE
auth_sessions = {}

@app.on_event("startup")
async def on_startup():
    await init_db()

@app.get("/auth/google")
async def login_google(telegram_id: str):
    flow = Flow.from_client_config(
        CLIENT_SECRETS, 
        scopes=SCOPES, 
        redirect_uri=os.getenv("GOOGLE_REDIRECT_URI")
    )
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
        state=telegram_id
    )
    
    # Сохраняем верификатор для конкретного пользователя
    auth_sessions[telegram_id] = flow.code_verifier
    
    return RedirectResponse(authorization_url)

@app.get("/auth/google/callback")
async def auth_callback(request: Request, state: str, code: str):
    telegram_id = state
    
    flow = Flow.from_client_config(
        CLIENT_SECRETS,
        scopes=SCOPES,
        redirect_uri=os.getenv("GOOGLE_REDIRECT_URI")
    )
    
    # Достаем верификатор из памяти
    code_verifier = auth_sessions.get(telegram_id)
    
    try:
        # Меняем код от Google на токены, используя верификатор
        flow.fetch_token(code=code, code_verifier=code_verifier)
    except Exception as e:
        return HTMLResponse(f"<h3>Ошибка обмена токена:</h3><p>{str(e)}</p>")
        
    credentials = flow.credentials
    refresh_token = credentials.refresh_token
    
    if not refresh_token:
        return HTMLResponse("<h3>Ошибка:</h3> Google не вернул Refresh Token. Попробуйте отозвать доступ к приложению в настройках Google и авторизоваться заново.")

    async with async_session() as session:
        result = await session.execute(select(User).filter_by(telegram_id=telegram_id))
        user = result.scalars().first()
        
        if user:
            user.google_refresh_token = refresh_token
        else:
            new_user = User(telegram_id=telegram_id, google_refresh_token=refresh_token)
            session.add(new_user)
        await session.commit()
        
    return HTMLResponse(f"<h2>Успех!</h2><p>Telegram ID <b>{telegram_id}</b> успешно привязан. Ваш Refresh Token сохранен в базе. Можете закрыть эту страницу и вернуться в бота.</p>")