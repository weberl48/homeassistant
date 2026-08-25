import React, { useEffect, useRef, useState } from "react";
import { FlatList, StyleSheet, Text, View } from "react-native";

import { api } from "../api";
import { T } from "../theme";

type Player = {
  id: string; name: string; pos: string; team?: string; bye?: number;
  tier?: number; vbd: number; adp?: number; survival?: number; score?: number;
  pick_no?: number; mine?: boolean;
};

export default function DraftBoard() {
  const [board, setBoard] = useState<any>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const fetching = useRef(false); // in-flight guard: never stack overlapping polls

  useEffect(() => {
    const load = async () => {
      if (fetching.current) return;
      fetching.current = true;
      try {
        setBoard(await api.board());
      } catch {
        /* wire indicator in App handles it */
      } finally {
        fetching.current = false;
      }
    };
    load();
    timer.current = setInterval(load, 2000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, []);

  if (!board) return <Text style={s.loading}>TAPPING THE WIRE…</Text>;

  const d = board.draft;
  const available: Player[] = board.players
    .filter((p: Player) => !p.pick_no)
    .sort((a: Player, b: Player) => (b.score ?? -999) - (a.score ?? -999))
    .slice(0, 80);
  const top = board.suggestions?.[0];

  return (
    <FlatList
      data={available}
      keyExtractor={(p) => p.id}
      ListHeaderComponent={
        <View>
          <View style={[s.clock, d.on_the_clock_me && s.clockMine]}>
            <Text style={[s.clockLine, d.on_the_clock_me && s.clockLineMine]}>
              {d.status === "complete"
                ? "DRAFT COMPLETE"
                : d.on_the_clock_me
                  ? `YOU'RE ON THE CLOCK — PICK ${d.current_pick}`
                  : `PICK ${d.current_pick} OF ${d.total_picks} · ROUND ${d.round}`}
            </Text>
            <Text style={s.clockSub}>
              {d.my_next_pick ? `you pick at #${d.my_next_pick}` : ""}
            </Text>
          </View>
          {top && (
            <View style={s.call}>
              <Text style={s.callTitle}>THE CALL</Text>
              <Text style={s.callName}>{top.name}</Text>
              <Text style={s.callMeta}>
                {top.pos} · {top.team} · score {top.score} · survives{" "}
                {Math.round((top.survival ?? 0) * 100)}%
              </Text>
              <Text style={s.callReason}>{top.reason}</Text>
            </View>
          )}
        </View>
      }
      renderItem={({ item: p, index }) => (
        <View style={s.row}>
          <Text style={s.rank}>{index + 1}</Text>
          <View style={[s.posDot, { backgroundColor: T.pos[p.pos] ?? T.inkFaint }]} />
          <View style={s.who}>
            <Text style={s.name} numberOfLines={1}>{p.name}</Text>
            <Text style={s.team}>{p.pos} · {p.team ?? ""} · bye {p.bye ?? "–"}</Text>
          </View>
          <View style={s.nums}>
            <Text style={s.num}>vbd {p.vbd}</Text>
            <Text style={s.numDim}>
              adp {p.adp ?? "–"} · {p.survival != null ? `${Math.round(p.survival * 100)}%` : "—"}
            </Text>
          </View>
        </View>
      )}
      style={{ backgroundColor: T.ground }}
      contentContainerStyle={{ paddingBottom: 40 }}
    />
  );
}

const s = StyleSheet.create({
  loading: { color: T.inkDim, textAlign: "center", marginTop: 60, letterSpacing: 2 },
  clock: {
    margin: 14, padding: 12, borderWidth: 1, borderColor: T.line,
    borderRadius: 6, backgroundColor: T.panel2,
  },
  clockMine: { borderColor: T.brass },
  clockLine: { color: T.ink, fontFamily: "monospace", fontSize: 14 },
  clockLineMine: { color: T.brassBright },
  clockSub: { color: T.inkDim, fontSize: 12, marginTop: 2 },
  call: {
    marginHorizontal: 14, marginBottom: 12, padding: 14,
    borderWidth: 1, borderColor: T.brassDeep, borderRadius: 6, backgroundColor: T.panel,
  },
  callTitle: { color: T.brass, fontSize: 11, letterSpacing: 3, fontWeight: "700" },
  callName: { color: T.ink, fontSize: 22, fontWeight: "700", marginTop: 6 },
  callMeta: { color: T.inkDim, fontFamily: "monospace", fontSize: 12, marginTop: 4 },
  callReason: { color: T.inkDim, marginTop: 6, lineHeight: 19 },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    paddingHorizontal: 14, paddingVertical: 9,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: T.line,
  },
  rank: { color: T.inkFaint, fontFamily: "monospace", width: 26, fontSize: 12 },
  posDot: { width: 8, height: 8, borderRadius: 2 },
  who: { flex: 1, minWidth: 0 },
  name: { color: T.ink, fontWeight: "600", fontSize: 15 },
  team: { color: T.inkFaint, fontSize: 11, letterSpacing: 0.5, marginTop: 1 },
  nums: { alignItems: "flex-end" },
  num: { color: T.ink, fontFamily: "monospace", fontSize: 12 },
  numDim: { color: T.inkFaint, fontFamily: "monospace", fontSize: 11, marginTop: 1 },
});
