module.exports = {
    apps: [
        {
            name: "duo-backend",
            script: ".venv/bin/python",
            args: "-m live run",
            cwd: "/opt/duo-live",
            env: {
                // 从 .env 文件加载，或直接写在这里
                // BINANCE_API_KEY: "",
                // BINANCE_API_SECRET: "",
                // TELEGRAM_BOT_TOKEN: "",
                // TELEGRAM_CHAT_ID: "",
                // TRADING_PASSWORD: "",
            },
            restart_delay: 5000,
            max_restarts: 50,
            autorestart: true,
            log_date_format: "YYYY-MM-DD HH:mm:ss",
        },
        {
            name: "duo-frontend",
            script: "node_modules/.bin/next",
            args: "start -H 0.0.0.0 -p 3000",
            cwd: "/opt/duo-live/web",
            restart_delay: 3000,
            autorestart: true,
            log_date_format: "YYYY-MM-DD HH:mm:ss",
        },
    ],
};
