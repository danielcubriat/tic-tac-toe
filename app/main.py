import os
from typing import Optional, List
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlmodel import Field, SQLModel, Session, create_engine, select
from passlib.context import CryptContext
from jose import jwt, JWTError

# Configuración
SECRET_KEY = os.getenv("SECRET_KEY", "tu-clave-secreta-cambiar-en-produccion")
ALGORITHM = "HS256"
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

# Modelos de base de datos
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    hashed_password: str
    wins: int = Field(default=0)
    losses: int = Field(default=0)
    draws: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Game(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    winner: Optional[str] = None
    player_x_id: Optional[int] = Field(default=None, foreign_key="user.id")
    player_o_id: Optional[int] = Field(default=None, foreign_key="user.id")
    moves: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)

# Conexión a base de datos
engine = create_engine(DATABASE_URL)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

app = FastAPI(title="Tic-Tac-Toe")

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

# Funciones de autenticación
def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def create_token(username: str) -> str:
    expire = datetime.utcnow() + timedelta(days=7)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Optional[User]:
    if not credentials:
        return None
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
        with Session(engine) as session:
            user = session.exec(select(User).where(User.username == username)).first()
            return user
    except JWTError:
        return None

# Schemas
class UserCreate(BaseModel):
    username: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class GameState(BaseModel):
    position: int
    board: List[str]
    current_player: str

# Endpoints de autenticación
@app.post("/register")
def register(user_data: UserCreate):
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.username == user_data.username)).first()
        if existing:
            raise HTTPException(400, "Usuario ya existe")
        user = User(username=user_data.username, hashed_password=hash_password(user_data.password))
        session.add(user)
        session.commit()
        return {"message": "Usuario creado", "token": create_token(user.username)}

@app.post("/login")
def login(user_data: UserLogin):
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == user_data.username)).first()
        if not user or not verify_password(user_data.password, user.hashed_password):
            raise HTTPException(401, "Credenciales inválidas")
        return {"message": "Login exitoso", "token": create_token(user.username)}

@app.get("/me")
def get_me(user: User = Depends(get_current_user)):
    if not user:
        raise HTTPException(401, "No autenticado")
    return {"username": user.username, "wins": user.wins, "losses": user.losses, "draws": user.draws}

# Lógica del juego
def check_winner(board: list) -> Optional[str]:
    lines = [
        [0, 1, 2], [3, 4, 5], [6, 7, 8],
        [0, 3, 6], [1, 4, 7], [2, 5, 8],
        [0, 4, 8], [2, 4, 6]
    ]
    for line in lines:
        a, b, c = line
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
    <head>
        <title>Tic-Tac-Toe v2</title>
        <style>
            body { font-family: Arial; text-align: center; padding: 30px; }
            .board { display: grid; grid-template-columns: repeat(3, 100px); gap: 5px; justify-content: center; margin: 20px auto; }
            .cell { width: 100px; height: 100px; font-size: 48px; cursor: pointer; background: #f0f0f0; border: none; }
            .cell:hover { background: #e0e0e0; }
            #status { font-size: 24px; margin: 20px; }
            button.action { padding: 10px 20px; font-size: 16px; cursor: pointer; margin: 5px; }
            #auth { margin-bottom: 20px; padding: 20px; background: #f9f9f9; border-radius: 10px; display: inline-block; }
            input { padding: 8px; margin: 5px; font-size: 14px; }
            #stats, #leaderboard { margin-top: 20px; padding: 20px; background: #f9f9f9; border-radius: 10px; display: inline-block; min-width: 250px; }
            #userInfo { color: green; font-weight: bold; }
            .hidden { display: none; }
        </style>
    </head>
    <body>
        <h1>Tic-Tac-Toe v2</h1>
        
        <div id="auth">
            <div id="loginForm">
                <h3>Iniciar Sesión / Registrarse</h3>
                <input type="text" id="username" placeholder="Usuario"><br>
                <input type="password" id="password" placeholder="Contraseña"><br>
                <button class="action" onclick="login()">Entrar</button>
                <button class="action" onclick="register()">Registrarse</button>
            </div>
            <div id="userInfo" class="hidden">
                <span id="welcomeMsg"></span>
                <button class="action" onclick="logout()">Salir</button>
            </div>
        </div>

        <div id="status">Turno: X</div>
        <div class="board" id="board"></div>
        <button class="action" onclick="resetGame()">Nuevo Juego</button>
        <button class="action" onclick="loadStats()">Mis Stats</button>
        <button class="action" onclick="loadLeaderboard()">Leaderboard</button>
        
        <div id="stats" class="hidden"></div>
        <div id="leaderboard" class="hidden"></div>

        <script>
            let board = Array(9).fill('');
            let currentPlayer = 'X';
            let gameOver = false;
            let token = localStorage.getItem('token');

            function updateAuthUI() {
                if (token) {
                    document.getElementById('loginForm').classList.add('hidden');
                    document.getElementById('userInfo').classList.remove('hidden');
                    fetchMe();
                } else {
                    document.getElementById('loginForm').classList.remove('hidden');
                    document.getElementById('userInfo').classList.add('hidden');
                }
            }

            async function fetchMe() {
                const res = await fetch('/me', { headers: { 'Authorization': 'Bearer ' + token } });
                if (res.ok) {
                    const data = await res.json();
                    document.getElementById('welcomeMsg').textContent = 'Hola, ' + data.username + '!';
                } else {
                    logout();
                }
            }

            async function login() {
                const username = document.getElementById('username').value;
                const password = document.getElementById('password').value;
                const res = await fetch('/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                const data = await res.json();
                if (res.ok) {
                    token = data.token;
                    localStorage.setItem('token', token);
                    updateAuthUI();
                } else {
                    alert(data.detail || 'Error al iniciar sesión');
                }
            }

            async function register() {
                const username = document.getElementById('username').value;
                const password = document.getElementById('password').value;
                const res = await fetch('/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                const data = await res.json();
                if (res.ok) {
                    token = data.token;
                    localStorage.setItem('token', token);
                    updateAuthUI();
                } else {
                    alert(data.detail || 'Error al registrarse');
                }
            }

            function logout() {
                token = null;
                localStorage.removeItem('token');
                updateAuthUI();
            }

            function render() {
                const boardEl = document.getElementById('board');
                boardEl.innerHTML = '';
                board.forEach((cell, i) => {
                    const btn = document.createElement('button');
                    btn.className = 'cell';
                    btn.textContent = cell;
                    btn.onclick = () => makeMove(i);
                    boardEl.appendChild(btn);
                });
            }

            async function makeMove(pos) {
                if (board[pos] || gameOver) return;
                const headers = { 'Content-Type': 'application/json' };
                if (token) headers['Authorization'] = 'Bearer ' + token;
                
                const res = await fetch('/move', {
                    method: 'POST',
                    headers,
                    body: JSON.stringify({ position: pos, board, current_player: currentPlayer })
                });
                const data = await res.json();
                board = data.board;
                currentPlayer = data.current_player;
                gameOver = data.game_over;
                document.getElementById('status').textContent = data.status;
                render();
            }

            function resetGame() {
                board = Array(9).fill('');
                currentPlayer = 'X';
                gameOver = false;
                document.getElementById('status').textContent = 'Turno: X';
                render();
            }

            async function loadStats() {
                if (!token) {
                    alert('Inicia sesión para ver tus stats');
                    return;
                }
                const res = await fetch('/me', { headers: { 'Authorization': 'Bearer ' + token } });
                if (res.ok) {
                    const data = await res.json();
                    document.getElementById('stats').classList.remove('hidden');
                    document.getElementById('stats').innerHTML = 
                        '<h3>Mis Estadísticas</h3>' +
                        '<p>Victorias: ' + data.wins + '</p>' +
                        '<p>Derrotas: ' + data.losses + '</p>' +
                        '<p>Empates: ' + data.draws + '</p>';
                }
            }

            async function loadLeaderboard() {
                const res = await fetch('/leaderboard');
                const data = await res.json();
                document.getElementById('leaderboard').classList.remove('hidden');
                let html = '<h3>Leaderboard</h3>';
                data.forEach((u, i) => {
                    html += '<p>' + (i+1) + '. ' + u.username + ' - ' + u.wins + ' victorias</p>';
                });
                document.getElementById('leaderboard').innerHTML = html;
            }

            updateAuthUI();
            render();
        </script>
    </body>
    </html>
    """

@app.post("/move")
def make_move(state: GameState, user: User = Depends(get_current_user)):
    board = state.board
    pos = state.position
    player = state.current_player

    if board[pos]:
        raise HTTPException(400, "Posición ocupada")

    board[pos] = player
    winner = check_winner(board)
    moves = sum(1 for cell in board if cell)
    
    if winner:
        with Session(engine) as session:
            game = Game(winner=winner, moves=moves)
            if user:
                if player == winner:
                    user.wins += 1
                else:
                    user.losses += 1
                session.add(user)
            session.add(game)
            session.commit()
        return {"board": board, "current_player": player, "game_over": True, "status": f"¡{winner} gana!"}
    
    if '' not in board:
        with Session(engine) as session:
            game = Game(winner="empate", moves=moves)
            if user:
                user.draws += 1
                session.add(user)
            session.add(game)
            session.commit()
        return {"board": board, "current_player": player, "game_over": True, "status": "¡Empate!"}
    
    next_player = 'O' if player == 'X' else 'X'
    return {"board": board, "current_player": next_player, "game_over": False, "status": f"Turno: {next_player}"}

@app.get("/leaderboard")
def get_leaderboard():
    with Session(engine) as session:
        users = session.exec(select(User).order_by(User.wins.desc()).limit(10)).all()
        return [{"username": u.username, "wins": u.wins} for u in users]

@app.get("/stats")
def get_stats():
    with Session(engine) as session:
        games = session.exec(select(Game)).all()
        total = len(games)
        x_wins = sum(1 for g in games if g.winner == "X")
        o_wins = sum(1 for g in games if g.winner == "O")
        draws = sum(1 for g in games if g.winner == "empate")
        return {"total": total, "x_wins": x_wins, "o_wins": o_wins, "draws": draws}