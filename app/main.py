import os
from typing import Optional, List
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlmodel import Field, SQLModel, Session, create_engine, select

# Modelo para guardar partidas
class Game(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    winner: Optional[str] = None  # "X", "O", o "empate"
    moves: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)

# Conexión a base de datos
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
engine = create_engine(DATABASE_URL)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

app = FastAPI(title="Tic-Tac-Toe")

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

class Move(BaseModel):
    position: int

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
        <title>Tic-Tac-Toe</title>
        <style>
            body { font-family: Arial; text-align: center; padding: 50px; }
            .board { display: grid; grid-template-columns: repeat(3, 100px); gap: 5px; justify-content: center; margin: 20px auto; }
            .cell { width: 100px; height: 100px; font-size: 48px; cursor: pointer; background: #f0f0f0; border: none; }
            .cell:hover { background: #e0e0e0; }
            #status { font-size: 24px; margin: 20px; }
            button.reset { padding: 10px 20px; font-size: 18px; cursor: pointer; margin: 5px; }
            #stats { margin-top: 30px; padding: 20px; background: #f9f9f9; border-radius: 10px; display: inline-block; }
        </style>
    </head>
    <body>
        <h1>Tic-Tac-Toe</h1>
        <div id="status">Turno: X</div>
        <div class="board" id="board"></div>
        <button class="reset" onclick="resetGame()">Nuevo Juego</button>
        <button class="reset" onclick="loadStats()">Ver Estadísticas</button>
        <div id="stats"></div>
        <script>
            let board = Array(9).fill('');
            let currentPlayer = 'X';
            let gameOver = false;

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
                const res = await fetch('/move', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
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
                const res = await fetch('/stats');
                const data = await res.json();
                document.getElementById('stats').innerHTML = 
                    '<h3>Estadísticas</h3>' +
                    '<p>Total partidas: ' + data.total + '</p>' +
                    '<p>Victorias X: ' + data.x_wins + '</p>' +
                    '<p>Victorias O: ' + data.o_wins + '</p>' +
                    '<p>Empates: ' + data.draws + '</p>';
            }

            render();
        </script>
    </body>
    </html>
    """

class GameState(BaseModel):
    position: int
    board: List[str]
    current_player: str

@app.post("/move")
def make_move(state: GameState):
    board = state.board
    pos = state.position
    player = state.current_player

    if board[pos]:
        raise HTTPException(400, "Posición ocupada")

    board[pos] = player
    winner = check_winner(board)
    moves = sum(1 for cell in board if cell)
    
    if winner:
        # Guardar partida en DB
        with Session(engine) as session:
            game = Game(winner=winner, moves=moves)
            session.add(game)
            session.commit()
        return {"board": board, "current_player": player, "game_over": True, "status": f"¡{winner} gana!"}
    
    if '' not in board:
        # Guardar empate en DB
        with Session(engine) as session:
            game = Game(winner="empate", moves=moves)
            session.add(game)
            session.commit()
        return {"board": board, "current_player": player, "game_over": True, "status": "¡Empate!"}
    
    next_player = 'O' if player == 'X' else 'X'
    return {"board": board, "current_player": next_player, "game_over": False, "status": f"Turno: {next_player}"}

@app.get("/stats")
def get_stats():
    with Session(engine) as session:
        games = session.exec(select(Game)).all()
        total = len(games)
        x_wins = sum(1 for g in games if g.winner == "X")
        o_wins = sum(1 for g in games if g.winner == "O")
        draws = sum(1 for g in games if g.winner == "empate")
        return {"total": total, "x_wins": x_wins, "o_wins": o_wins, "draws": draws}