from .cli import main

if __name__ == "__main__":
    # cli.main 内部以 sys.exit 设置退出码（0 成功，2 输入/校验失败）。
    main()
