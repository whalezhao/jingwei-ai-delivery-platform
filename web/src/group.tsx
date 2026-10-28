import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";

/** 顶栏研发组快切：null = 全部研发组（默认，持久化在 localStorage）。 */
interface Group {
  id: number;
  name: string;
}
interface GroupCtx {
  groups: Group[];
  groupId: number | null;
  setGroupId: (id: number | null) => void;
}

const KEY = "vega_group";

const Ctx = createContext<GroupCtx>({ groups: [], groupId: null, setGroupId: () => {} });

export function GroupProvider({ children }: { children: ReactNode }) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [groupId, setGroupIdState] = useState<number | null>(() => {
    const n = Number(localStorage.getItem(KEY));
    return Number.isFinite(n) && n > 0 ? n : null;
  });

  useEffect(() => {
    api<Group[]>("/api/groups").then((gs) => {
      setGroups(gs);
      // 存过的组被删了 → 回到全部
      setGroupIdState((cur) => {
        if (cur && !gs.some((g) => g.id === cur)) {
          localStorage.removeItem(KEY);
          return null;
        }
        return cur;
      });
    });
  }, []);

  const value = useMemo<GroupCtx>(
    () => ({
      groups,
      groupId,
      setGroupId: (id) => {
        setGroupIdState(id);
        if (id) localStorage.setItem(KEY, String(id));
        else localStorage.removeItem(KEY);
      },
    }),
    [groups, groupId]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export const useGroup = () => useContext(Ctx);
