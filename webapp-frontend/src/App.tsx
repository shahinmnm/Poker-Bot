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

interface TelegramWebApp {
  ready: () => void;
  expand: () => void;
  initData?: string;
  initDataUnsafe?: {
    user?: TelegramUser;
  };
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}

type AuthStatus = "idle" | "loading" | "success" | "error";

const AUTH_ENDPOINT = "https://poker.shahin8n.sbs/auth/telegram";

interface AuthSuccessResponse {
  success: true;
  token: string;
  user: TelegramUser;
}

interface AuthErrorResponse {
  detail?: string;
  message?: string;
  error?: string;
}

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
  const [sessionToken, setSessionToken] = useState<string | null>(null);

  useEffect(() => {
    const tg = window.Telegram?.WebApp;

    if (!tg) {
      setError("This app must be opened inside Telegram.");
      setAuthStatus("error");
      return;
    }

    tg.ready();
    tg.expand();

    const unsafeUser = tg.initDataUnsafe?.user;
    const initData = tg.initData ?? "";

    if (unsafeUser) {
      setTelegramUser(unsafeUser);
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
      setSessionToken(null);

      try {
        const response = await fetch(AUTH_ENDPOINT, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            initData,
            user: unsafeUser,
          }),
        });

        if (!response.ok) {
          const errorPayload = (await response
            .json()
            .catch(() => null)) as AuthErrorResponse | null;
          const errorMessage =
            errorPayload?.detail ||
            errorPayload?.message ||
            errorPayload?.error ||
            `Request failed with status ${response.status}.`;
          throw new Error(errorMessage);
        }

        const json = (await response.json()) as AuthSuccessResponse;

        setAuthStatus("success");
        setAuthMessage("Telegram token verified successfully.");
        setSessionToken(json.token);
        setTelegramUser(json.user ?? unsafeUser ?? null);
        setError(null);
      } catch (fetchError) {
        const message =
          fetchError instanceof Error
            ? fetchError.message
            : "An error occurred while contacting the server.";
        setAuthStatus("error");
        setAuthMessage(null);
        setSessionToken(null);
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
        {sessionToken && (
          <p className="info">
            Session token: <code>{sessionToken}</code>
          </p>
        )}
        {error && <p className="error">{error}</p>}
      </section>
    </div>
  );
}

export default App;
