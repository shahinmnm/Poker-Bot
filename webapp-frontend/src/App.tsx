import { useEffect, useMemo, useState } from "react";
import "./App.css";

interface TelegramUser {
  id: number;
  first_name: string;
  last_name?: string;
  username?: string;
  language_code?: string;
  photo_url?: string;
}

interface TelegramInitDataUnsafe {
  user?: TelegramUser;
}

interface TelegramWebApp {
  ready: () => void;
  expand: () => void;
  initData?: string;
  initDataUnsafe?: TelegramInitDataUnsafe;
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}

type AuthStatus = "idle" | "loading" | "success" | "error";

const AUTH_ENDPOINT = "https://poker.shahin8n.sbs/api/auth/telegram";

const formatDisplayName = (user: TelegramUser) => {
  const name = [user.first_name, user.last_name].filter(Boolean).join(" ");
  if (name) {
    return name;
  }
  if (user.username) {
    return `@${user.username}`;
  }
  return "Telegram user";
};

function App() {
  const [telegramUser, setTelegramUser] = useState<TelegramUser | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus>("idle");
  const [authMessage, setAuthMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const tg = window.Telegram?.WebApp;

    if (!tg) {
      setError("This app must be opened inside Telegram.");
      setAuthStatus("error");
      return;
    }

    tg.ready();
    tg.expand();

    const unsafeData = tg.initDataUnsafe;
    const initData = tg.initData ?? "";

    if (unsafeData?.user) {
      setTelegramUser(unsafeData.user);
      setError(null);
    } else {
      setError("Telegram user information is not available.");
    }

    if (!initData) {
      setAuthStatus("error");
      setAuthMessage(null);
      setError("No valid token was provided by Telegram.");
      return;
    }

    const authenticateWithBackend = async () => {
      setAuthStatus("loading");
      setAuthMessage(null);
      setError(null);

      try {
        const response = await fetch(AUTH_ENDPOINT, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ initData }),
        });

        const rawPayload = await response.text();
        let parsedMessage: string | null = null;

        if (rawPayload) {
          try {
            const jsonPayload = JSON.parse(rawPayload);
            if (typeof jsonPayload === "string") {
              parsedMessage = jsonPayload;
            } else if (typeof jsonPayload?.message === "string") {
              parsedMessage = jsonPayload.message;
            } else {
              parsedMessage = JSON.stringify(jsonPayload, null, 2);
            }
          } catch (parseError) {
            parsedMessage = rawPayload;
          }
        }

        if (!response.ok) {
          throw new Error(
            parsedMessage ??
              `Request failed with status ${response.status}.`
          );
        }

        setAuthStatus("success");
        setAuthMessage(
          parsedMessage ?? "Telegram token verified successfully."
        );
        setError(null);
      } catch (fetchError) {
        const message =
          fetchError instanceof Error
            ? fetchError.message
            : "An error occurred while contacting the server.";
        setAuthStatus("error");
        setAuthMessage(null);
        setError(message);
      }
    };

    authenticateWithBackend();
  }, []);

  const statusMessage = useMemo(() => {
    switch (authStatus) {
      case "loading":
        return "Sending token to the server...";
      case "success":
        return "Connected to the server successfully.";
      case "error":
        return "Authentication failed.";
      default:
        return "Waiting to receive data from Telegram.";
    }
  }, [authStatus]);

  return (
    <div className="app">
      <header className="app__header">
        <h1 className="app__title">Poker WebApp</h1>
        <p className="subtitle">
          Authenticate via the Telegram Web App
        </p>
      </header>

      <section className="card">
        {telegramUser ? (
          <div className="user-card">
            {telegramUser.photo_url ? (
              <img
                src={telegramUser.photo_url}
                alt={formatDisplayName(telegramUser)}
                className="user-card__avatar"
                referrerPolicy="no-referrer"
              />
            ) : (
              <div className="user-card__avatar user-card__avatar--placeholder">
                {telegramUser.first_name.charAt(0)}
              </div>
            )}
            <div className="user-card__details">
              <h2 className="user-card__name">
                {formatDisplayName(telegramUser)}
              </h2>
              {telegramUser.username && (
                <p className="user-card__username">@{telegramUser.username}</p>
              )}
              <dl className="user-card__meta">
                <div>
                  <dt>ID</dt>
                  <dd>{telegramUser.id}</dd>
                </div>
                {telegramUser.language_code && (
                  <div>
                    <dt>Language</dt>
                    <dd>{telegramUser.language_code.toUpperCase()}</dd>
                  </div>
                )}
              </dl>
            </div>
          </div>
        ) : (
          <p className="info">Waiting for Telegram user information...</p>
        )}
      </section>

      <section className="card status-card">
        <h2>Authentication Status</h2>
        <p className={`status status--${authStatus}`}>{statusMessage}</p>
        {authMessage && authStatus === "success" && (
          <pre className="payload">{authMessage}</pre>
        )}
        {error && <p className="error">{error}</p>}
      </section>
    </div>
  );
}

export default App;
