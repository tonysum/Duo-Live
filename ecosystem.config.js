module.exports = {
    apps: [

        {
            name: "duo-live-backend",
            script: ".venv/bin/python",
            args: "-m live run",
            cwd: "./",
            env: {
                // 从 .env 文件加载，或直接写在这里
                // BINANCE_API_KEY: "",
                // BINANCE_API_SECRET: "",
                // TELEGRAM_BOT_TOKEN: "",
                // TELEGRAM_CHAT_ID: "",
                // TRADING_PASSWORD: "",
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
                PORT: 3000,
                HOSTNAME: "0.0.0.0",
            },
            restart_delay: 3000,
            max_restarts: 10,
            autorestart: true,
            log_date_format: "YYYY-MM-DD HH:mm:ss",
        },
    ],
};
