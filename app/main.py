import os
import json
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect
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

# ==================== MULTIPLAYER ====================

class GameRoom:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.players: Dict[str, WebSocket] = {}  # username -> websocket
        self.board = [''] * 9
        self.current_player = 'X'
        self.player_x: Optional[str] = None
        self.player_o: Optional[str] = None
        self.game_over = False

    def add_player(self, username: str, websocket: WebSocket) -> str:
        if self.player_x is None:
            self.player_x = username
            self.players[username] = websocket
            return 'X'
        elif self.player_o is None:
            self.player_o = username
            self.players[username] = websocket
            return 'O'
        return ''

    def is_full(self) -> bool:
        return self.player_x is not None and self.player_o is not None

    def get_symbol(self, username: str) -> str:
        if username == self.player_x:
            return 'X'
        elif username == self.player_o:
            return 'O'
        return ''

    def check_winner(self) -> Optional[str]:
        lines = [
            [0, 1, 2], [3, 4, 5], [6, 7, 8],
            [0, 3, 6], [1, 4, 7], [2, 5, 8],
            [0, 4, 8], [2, 4, 6]
        ]
        for line in lines:
            a, b, c = line
            if self.board[a] and self.board[a] == self.board[b] == self.board[c]:
                return self.board[a]
        return None

    def make_move(self, username: str, position: int) -> dict:
        symbol = self.get_symbol(username)
        if symbol != self.current_player:
            return {"error": "No es tu turno"}
        if self.board[position]:
            return {"error": "Posición ocupada"}
        if self.game_over:
            return {"error": "Juego terminado"}

        self.board[position] = symbol
        winner = self.check_winner()

        if winner:
            self.game_over = True
            return {"board": self.board, "winner": winner, "game_over": True}

        if '' not in self.board:
            self.game_over = True
            return {"board": self.board, "winner": "empate", "game_over": True}

        self.current_player = 'O' if self.current_player == 'X' else 'X'
        return {"board": self.board, "current_player": self.current_player, "game_over": False}

# Salas activas
rooms: Dict[str, GameRoom] = {}
waiting_room: Optional[str] = None

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, username: str):
        await websocket.accept()
        self.active_connections[username] = websocket

    def disconnect(self, username: str):
        if username in self.active_connections:
            del self.active_connections[username]

    async def send_personal(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast_to_room(self, room: GameRoom, message: dict):
        for ws in room.players.values():
            await ws.send_json(message)

manager = ConnectionManager()

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str):
    global waiting_room
    await manager.connect(websocket, username)

    room: Optional[GameRoom] = None

    try:
        # Buscar o crear sala
        if waiting_room and waiting_room in rooms and not rooms[waiting_room].is_full():
            room = rooms[waiting_room]
            symbol = room.add_player(username, websocket)
            waiting_room = None
            await manager.broadcast_to_room(room, {
                "type": "game_start",
                "player_x": room.player_x,
                "player_o": room.player_o,
                "board": room.board,
                "current_player": room.current_player
            })
        else:
            room_id = f"room_{len(rooms)}_{datetime.utcnow().timestamp()}"
            room = GameRoom(room_id)
            symbol = room.add_player(username, websocket)
            rooms[room_id] = room
            waiting_room = room_id
            await manager.send_personal({"type": "waiting", "message": "Esperando oponente..."}, websocket)

        while True:
            data = await websocket.receive_json()
            if data["type"] == "move" and room:
                result = room.make_move(username, data["position"])
                if "error" not in result:
                    await manager.broadcast_to_room(room, {"type": "update", **result})
                    if result.get("game_over"):
                        # Guardar resultado en DB
                        with Session(engine) as session:
                            winner_symbol = result.get("winner")
                            if winner_symbol and winner_symbol != "empate":
                                winner_name = room.player_x if winner_symbol == "X" else room.player_o
                                loser_name = room.player_o if winner_symbol == "X" else room.player_x
                                winner_user = session.exec(select(User).where(User.username == winner_name)).first()
                                loser_user = session.exec(select(User).where(User.username == loser_name)).first()
                                if winner_user:
                                    winner_user.wins += 1
                                    session.add(winner_user)
                                if loser_user:
                                    loser_user.losses += 1
                                    session.add(loser_user)
                            elif winner_symbol == "empate":
                                for pname in [room.player_x, room.player_o]:
                                    puser = session.exec(select(User).where(User.username == pname)).first()
                                    if puser:
                                        puser.draws += 1
                                        session.add(puser)
                            session.commit()
                else:
                    await manager.send_personal({"type": "error", **result}, websocket)

    except WebSocketDisconnect:
        manager.disconnect(username)
        if room:
            other_player = room.player_o if username == room.player_x else room.player_x
            if other_player and other_player in room.players:
                await manager.send_personal({"type": "opponent_left"}, room.players[other_player])
            if room.room_id in rooms:
                del rooms[room.room_id]

# ==================== ENDPOINTS ====================

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

@app.get("/leaderboard")
def get_leaderboard():
    with Session(engine) as session:
        users = session.exec(select(User).order_by(User.wins.desc()).limit(10)).all()
        return [{"username": u.username, "wins": u.wins} for u in users]

# Lógica del juego single player
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

@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <html>
    <head>
        <title>Tic-Tac-Toe v3</title>
        <style>
            body { font-family: Arial; text-align: center; padding: 20px; }
            .board { display: grid; grid-template-columns: repeat(3, 100px); gap: 5px; justify-content: center; margin: 20px auto; }
            .cell { width: 100px; height: 100px; font-size: 48px; cursor: pointer; background: #f0f0f0; border: none; }
            .cell:hover { background: #e0e0e0; }
            .cell:disabled { cursor: not-allowed; }
            #status { font-size: 24px; margin: 20px; }
            button.action { padding: 10px 20px; font-size: 16px; cursor: pointer; margin: 5px; }
            #auth { margin-bottom: 20px; padding: 20px; background: #f9f9f9; border-radius: 10px; display: inline-block; }
            input { padding: 8px; margin: 5px; font-size: 14px; }
            #stats, #leaderboard { margin-top: 20px; padding: 20px; background: #f9f9f9; border-radius: 10px; display: inline-block; min-width: 250px; }
            #userInfo { color: green; font-weight: bold; }
            .hidden { display: none; }
            .tab { padding: 10px 20px; cursor: pointer; background: #ddd; border: none; margin: 2px; }
            .tab.active { background: #4CAF50; color: white; }
            #modeSelector { margin: 20px; }
        </style>
    </head>
    <body>
        <h1>Tic-Tac-Toe v3</h1>
        
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

        <div id="modeSelector">
            <button class="tab active" id="tabSingle" onclick="setMode('single')">Un Jugador</button>
            <button class="tab" id="tabMulti" onclick="setMode('multi')">Multijugador</button>
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
            let currentUser = null;
            let mode = 'single';
            let ws = null;
            let mySymbol = '';

            function updateAuthUI() {
                if (token) {
                    document.getElementById('loginForm').classList.add('hidden');
                    document.getElementById('userInfo').classList.remove('hidden');
                    fetchMe();
                } else {
                    document.getElementById('loginForm').classList.remove('hidden');
                    document.getElementById('userInfo').classList.add('hidden');
                    currentUser = null;
                }
            }

            async function fetchMe() {
                const res = await fetch('/me', { headers: { 'Authorization': 'Bearer ' + token } });
                if (res.ok) {
                    const data = await res.json();
                    currentUser = data.username;
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
                currentUser = null;
                localStorage.removeItem('token');
                if (ws) ws.close();
                updateAuthUI();
            }

            function setMode(newMode) {
                mode = newMode;
                document.getElementById('tabSingle').classList.toggle('active', mode === 'single');
                document.getElementById('tabMulti').classList.toggle('active', mode === 'multi');
                
                if (mode === 'multi') {
                    if (!currentUser) {
                        alert('Inicia sesión para jugar multijugador');
                        setMode('single');
                        return;
                    }
                    connectWebSocket();
                } else {
                    if (ws) ws.close();
                    resetGame();
                }
            }

            function connectWebSocket() {
                const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                ws = new WebSocket(protocol + '//' + window.location.host + '/ws/' + currentUser);
                
                ws.onmessage = function(event) {
                    const data = JSON.parse(event.data);
                    
                    if (data.type === 'waiting') {
                        document.getElementById('status').textContent = data.message;
                        gameOver = true;
                    } else if (data.type === 'game_start') {
                        mySymbol = data.player_x === currentUser ? 'X' : 'O';
                        board = data.board;
                        currentPlayer = data.current_player;
                        gameOver = false;
                        document.getElementById('status').textContent = 
                            'Juego iniciado! Eres ' + mySymbol + '. ' +
                            (currentPlayer === mySymbol ? 'Tu turno' : 'Turno del oponente');
                        render();
                    } else if (data.type === 'update') {
                        board = data.board;
                        if (data.game_over) {
                            gameOver = true;
                            if (data.winner === 'empate') {
                                document.getElementById('status').textContent = '¡Empate!';
                            } else {
                                const winnerIsMe = data.winner === mySymbol;
                                document.getElementById('status').textContent = winnerIsMe ? '¡Ganaste!' : '¡Perdiste!';
                            }
                        } else {
                            currentPlayer = data.current_player;
                            document.getElementById('status').textContent = 
                                currentPlayer === mySymbol ? 'Tu turno' : 'Turno del oponente';
                        }
                        render();
                    } else if (data.type === 'opponent_left') {
                        document.getElementById('status').textContent = 'El oponente se desconectó';
                        gameOver = true;
                    } else if (data.type === 'error') {
                        alert(data.error);
                    }
                };

                ws.onclose = function() {
                    if (mode === 'multi') {
                        document.getElementById('status').textContent = 'Desconectado';
                    }
                };
            }

            function render() {
                const boardEl = document.getElementById('board');
                boardEl.innerHTML = '';
                board.forEach((cell, i) => {
                    const btn = document.createElement('button');
                    btn.className = 'cell';
                    btn.textContent = cell;
                    btn.onclick = () => makeMove(i);
                    if (mode === 'multi' && (gameOver || currentPlayer !== mySymbol)) {
                        btn.disabled = true;
                    }
                    boardEl.appendChild(btn);
                });
            }

            async function makeMove(pos) {
                if (board[pos] || gameOver) return;

                if (mode === 'multi') {
                    if (currentPlayer !== mySymbol) return;
                    ws.send(JSON.stringify({ type: 'move', position: pos }));
                } else {
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
            }

            function resetGame() {
                if (mode === 'multi' && ws) {
                    ws.close();
                    connectWebSocket();
                } else {
                    board = Array(9).fill('');
                    currentPlayer = 'X';
                    gameOver = false;
                    document.getElementById('status').textContent = 'Turno: X';
                    render();
                }
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
                if (data.length === 0) {
                    html += '<p>No hay jugadores aún</p>';
                } else {
                    data.forEach((u, i) => {
                        html += '<p>' + (i+1) + '. ' + u.username + ' - ' + u.wins + ' victorias</p>';
                    });
                }
                document.getElementById('leaderboard').innerHTML = html;
            }

            updateAuthUI();
            render();
        </script>
    </body>
    </html>
    """

# @app.get("/reset-db") #Al parecer era cuestión de una vez tenerlo
# def reset_db():
#     SQLModel.metadata.drop_all(engine)
#     SQLModel.metadata.create_all(engine)
#     return {"message": "Database reset"}