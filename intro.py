import asyncio
import json
import websockets

import http
import os
import signal

from websockets.asyncio.server import serve, broadcast

# How to run:
# conda activate websockets
# cd to directory
# python -m http.server &
# python intro.py
# go to localhost 8000 in browser, this'll start index.html which'll connect to this server program

lobby = None
games = []

class Message():
    def __init__(self):
        self.msg = {}

    async def send(self, target):
        try:
            await target.send(json.dumps(self.msg))
        except websockets.exceptions.ConnectionClosedOK:
            return False
        except websockets.exceptions.ConnectionClosedError:
            return False
        return True

class MessageLobby(Message):
    def __init__(self):
        self.msg = {
            "msgId": 0
        }

class MessageGameFound(Message):
    def __init__(self):
        self.msg = {
            "msgId": 1
        }

class MessageYourMove(Message):
    def __init__(self, xo, board):
        self.msg = {
            "msgId": 2,
            "piece": xo,
            "board": board
        }

class MessageOpponentsMove(Message):
    def __init__(self, xo, board):
        self.msg = {
            "msgId": 3,
            "piece": xo,
            "board": board
        }

class MessageGameOver(Message):
    def __init__(self, board, winner, you):
        self.msg = {
            "msgId": 4,
            "board": board,
            "winner": winner,
            "you": you
        }

class MessageDisconnected(Message):
    # opponent disconnection (not server disconnection)
    def __init__(self):
        self.msg = {
            "msgId": 5
        }

class MessagePing(Message):
    def __init__(self):
        self.msg = {}

class Game():
    def __init__(self, p1Id, p2Id):
        self.p1Id = p1Id
        self.p2Id = p2Id
        self.crtId = p1Id
        self.winner = None
        self.transitionMode = True
        self.board = [['-', '-', '-'], ['-', '-', '-'], ['-', '-', '-']]
        self.active = True # both players are connected
    
    def gameover(self):
        # check for a winner
        for i in range(3):
            # check row
            if ((self.board[i][0] == self.board[i][1]) and (self.board[i][1] == self.board[i][2]) and (self.board[i][2] != '-')):
                return 0
            # check column
            if ((self.board[0][i] == self.board[1][i]) and (self.board[1][i] == self.board[2][i]) and (self.board[2][i] != '-')):
                return 0
        # main diagonal
        if ((self.board[0][0] == self.board[1][1]) and (self.board[1][1] == self.board[2][2]) and (self.board[2][2] != '-')):
            return 0
        # secondary diagonal
        if ((self.board[0][2] == self.board[1][1]) and (self.board[1][1] == self.board[2][0]) and (self.board[2][0] != '-')):
            return 0
        
        # check for a tie
        if ((self.board[0][0] != '-') and (self.board[1][0] != '-') and (self.board[2][0] != '-')
            and
            (self.board[0][1] != '-') and (self.board[1][1] != '-') and (self.board[2][1] != '-')
            and
            (self.board[0][2] != '-') and (self.board[1][2] != '-') and (self.board[2][2] != '-')):
            return 1
        
        # not a winner or a tie
        return 2

def healthCheck(connection, request):
    if request.path == "/healthz":
        return connection.respond(http.HTTPStatus.OK, "OK\n")

async def clearSocket(websocket, game):
    while True:
        try:
            await asyncio.wait_for(websocket.recv(), timeout=1)
        except asyncio.TimeoutError:
            game.transitionMode = False
            break
        except websockets.exceptions.ConnectionClosedOK:
            game.active = False
            break
        except websockets.exceptions.ConnectionClosedError:
            game.active = False
            break

async def handler(websocket):
    global lobby

    if lobby != None:
        if (await MessagePing().send(lobby)):
            games.append(Game(lobby.id, websocket.id))
            lobby = None
        else:
            # overwrite the old socket; it's disconnected
            lobby = websocket
    else:
        # wait for a 2nd player
        lobby = websocket
     
    # tell client they're in lobby
    if (not (await MessageLobby().send(websocket))):
        return

    # poll for a game
    game = None
    while (game is None):
        await asyncio.sleep(1)
        for g in games:
            if ((g.p1Id == websocket.id) or (g.p2Id == websocket.id)):
                game = g
                break
    
    # tell client game's starting
    g.active &= (await MessageGameFound().send(websocket))

    # game loop
    sendYourMoveMsg = False
    while ((g.winner is None) and g.active):
        await asyncio.sleep(0.05)

        # transition between players
        if g.transitionMode == True:
            sendYourMoveMsg = False
            await clearSocket(websocket, g)
            if not (g.winner is None):
                break

        # wait for player to make their move & process it
        if g.crtId == websocket.id:

            if not sendYourMoveMsg:
                # tell client it's their turn
                if (websocket.id == g.p1Id):
                    g.active &= (await MessageYourMove("X", g.board).send(websocket))
                else:
                    g.active &= (await MessageYourMove("O", g.board).send(websocket))
                
                if (not g.active):
                    break
                
                sendYourMoveMsg = True

            # get move from client (client code only accepts valid moves)
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=1)
                msg = json.loads(msg)
            except asyncio.TimeoutError:
                # force to top of loop to check if g.active
                continue
            except websockets.exceptions.ConnectionClosedOK:
                game.active = False
                break
            except websockets.exceptions.ConnectionClosedError:
                game.active = False
                break
    
            # update the board with the newest move
            if (g.crtId == g.p1Id):
                g.board[msg["row"]][msg["col"]] = 'X'
            else:
                g.board[msg["row"]][msg["col"]] = 'O'

            # tell client their turn's over
            if (websocket.id == g.p1Id):
                g.active &= (await MessageOpponentsMove("X", g.board).send(websocket))
            else:
                g.active &= (await MessageOpponentsMove("O", g.board).send(websocket))
            
            if (not g.active):
                break

            # swap players, set to transition mode
            if (g.crtId == g.p1Id):
                g.crtId = g.p2Id
            else:
                g.crtId = g.p1Id
            g.transitionMode = True
            
            # check game over
            state = g.gameover() # 0 = winner | 1 = tie | 2 = not over
            if (state == 0):
                g.winner = websocket.id
            elif(state == 1):
                g.winner = "tie"
        else:
            if (websocket.id == g.p1Id):
                g.active &= (await MessageOpponentsMove("X", g.board).send(websocket))
            else:
                g.active &= (await MessageOpponentsMove("O", g.board).send(websocket))
            
    
    # doesn't matter if these succeed or not because it's the end
    if (not g.active):
        await MessageDisconnected().send(websocket)
    else:
        await MessageGameOver(g.board, str(g.winner), str(websocket.id)).send(websocket)


async def main():
    # # seems like this is the server's socket ""=127.0.0.1:8001
    # async with serve(handler, "", 8001) as server:
    #     await server.wait_closed()
    port = int(os.environ.get("PORT", "8001"))
    async with serve(handler, "", port, process_request=healthCheck) as server:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, server.close)
        await server.wait_closed()


asyncio.run(main())