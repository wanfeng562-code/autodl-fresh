import os

# GUI tests do not create real desktop windows or modify Windows startup settings.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
