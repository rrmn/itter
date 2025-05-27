from collections import deque

MAX_HISTORY_SIZE = 10


class CommandHistory:
    def __init__(self):
        self.history = deque(maxlen=MAX_HISTORY_SIZE)
        self.cursor = -1

    def add(self, command: str):
        # Reset cursor
        self.cursor = -1

        # Avoid adding duplicate commands
        if command == self.peek():
            return

        if len(self.history) == self.history.maxlen:
            self.history.pop()
        self.history.appendleft(command)

    def scroll_up(self):
        if len(self.history) == 0:
            return ""

        if self.cursor < len(self.history) - 1:
            self.cursor += 1
            return self.history[self.cursor]

        return self.history[-1]

    def scroll_down(self):
        if len(self.history) == 0 or self.cursor <= 0:
            self.cursor = max(-1, self.cursor - 1)
            return ""

        self.cursor -= 1
        return self.history[self.cursor]

    def peek(self):
        if len(self.history) == 0:
            return ""

        return self.history[0]
