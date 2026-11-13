from app.settings import load_settings


def main():
    s = load_settings()["server"]
    print(f"listening on {s['host']}:{s['port']} with {s['workers']} workers")


if __name__ == "__main__":
    main()
