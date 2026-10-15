"""Entry point for Visual Code Builder. Run: python main.py [file.py]"""
import sys

from vcb.app import App


def run():
    app = App()
    if len(sys.argv) > 1:
        app.open_file(sys.argv[1])
    app.mainloop()


if __name__ == "__main__":
    run()
