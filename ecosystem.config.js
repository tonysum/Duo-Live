module.exports = {
    apps: [

        {
            name: "duo-live-backend",
            script: ".venv/bin/python",
            args: "-m live run --auto-trade",
            cwd: "./",
            // 与仓库根目录 .env 二选一或叠加：进程启动后 __main__ 会 load_dotenv()，
            // 故多数密钥/CORS 写在 .env 即可；此处 env 会覆盖同名变量。
            env: {
                TZ: "UTC",
                // 浏览器访问「前端 :3000 + API :8899」时必须配置，否则跨域被拒：
                // CORS_ORIGINS: "http://你的公网IP:3000,http://你的公网IP",
                // 调试可临时：CORS_ORIGINS: "*"
                // BINANCE_* / TELEGRAM_* 等建议放 .env，勿提交仓库
            },
            restart_delay: 5000,
            max_restarts: 10,
            min_uptime: 10000,
            exp_backoff_restart_delay: 100,
            autorestart: true,
            log_date_format: "YYYY-MM-DD HH:mm:ss",
        },
        {
            name: "duo-live-frontend",
            script: "bash",
            args: "start.sh",
            cwd: "./web",
            env: {
                TZ: "UTC",
                PORT: 3000,
            },
            restart_delay: 3000,
            max_restarts: 10,
            autorestart: true,
            log_date_format: "YYYY-MM-DD HH:mm:ss",
        },
    ],
};
