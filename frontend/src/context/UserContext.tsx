import { createContext, useCallback, useContext, useEffect, useState } from "react";

import { clearTokens, me, Me } from "../api/client";

interface UserContextValue {
  me: Me | null;
  loading: boolean;
  reload: () => Promise<void>;
  logout: () => void;
}

const UserContext = createContext<UserContextValue | undefined>(undefined);

export function UserProvider({ children }: { children: React.ReactNode }) {
  const [profile, setProfile] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const p = await me();
      setProfile(p);
    } catch {
      clearTokens();
      setProfile(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Clears the tokens AND the cached profile, so the next login cannot render
  // with the previous user's role.
  const logout = useCallback(() => {
    clearTokens();
    setProfile(null);
  }, []);

  return (
    <UserContext.Provider value={{ me: profile, loading, reload, logout }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser(): UserContextValue {
  const ctx = useContext(UserContext);
  if (!ctx) throw new Error("useUser must be used within a UserProvider");
  return ctx;
}
