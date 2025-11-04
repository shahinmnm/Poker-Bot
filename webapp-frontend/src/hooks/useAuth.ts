import { useState, useEffect } from 'react';
import { authenticateWithTelegram } from '../services/api';
import { useTelegram } from './useTelegram';

export const useAuth = () => {
  const { initData } = useTelegram();
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const authenticate = async () => {
      try {
        // Check for existing session
        const existingToken = localStorage.getItem('session_token');
        if (existingToken) {
          setSessionToken(existingToken);
          setIsAuthenticated(true);
          setLoading(false);
          return;
        }

        // Authenticate with Telegram
        if (initData) {
          const response = await authenticateWithTelegram(initData);
          if (response.success) {
            setSessionToken(response.session_token);
            setIsAuthenticated(true);
            localStorage.setItem('session_token', response.session_token);
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Authentication failed');
      } finally {
        setLoading(false);
      }
    };

    authenticate();
  }, [initData]);

  const logout = () => {
    localStorage.removeItem('session_token');
    setSessionToken(null);
    setIsAuthenticated(false);
  };

  return {
    isAuthenticated,
    sessionToken,
    loading,
    error,
    logout,
  };
};
