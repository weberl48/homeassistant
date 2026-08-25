import React, { useEffect, useRef, useState } from "react";
import { FlatList, StyleSheet, Text, View } from "react-native";

import { api } from "../api";
import { T } from "../theme";

type Target = {
  id: string; name: string; pos: string; team?: string;
  fa_score: number; bid: number; hard_confirm: boolean;
  heat: number; lineup_gain: number | null;
};

export default function Waivers() {
  const [data, setData] = useState<any>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const fetching = useRef(false); // in-flight guard: never stack overlapping polls

  useEffect(() => {
    const load = async () => {
      if (fetching.current) return;
      fetching.current = true;
      try {
        setData(await api.waivers());
      } catch {
        /* wire indicator in App handles it */
      } finally {
        fetching.current = false;
      }
    };
    load();
    // Mirrors the web board's waiver poll cadence — the street moves fast
    // enough to be worth 15s, but not fast enough to hammer the Pi.
    timer.current = setInterval(load, 15000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, []);

  if (!data) return <Text style={s.loading}>WALKING THE STREET…</Text>;

  const targets: Target[] = data.targets ?? [];

  return (
    <FlatList
      data={targets}
      keyExtractor={(t) => t.id}
      ListHeaderComponent={
        targets.length === 0 ? (
          <View style={s.empty}>
            <Text style={s.emptyText}>{data.note}</Text>
          </View>
        ) : null
      }
      renderItem={({ item: t, index }) => (
        <View style={s.row}>
          <Text style={s.rank}>{index + 1}</Text>
          <View style={[s.posDot, { backgroundColor: T.pos[t.pos] ?? T.inkFaint }]} />
          <View style={s.who}>
            <Text style={s.name} numberOfLines={1}>{t.name}</Text>
            <Text style={s.team}>{t.pos} · {t.team ?? "FA"}</Text>
            <Text style={t.lineup_gain != null && t.lineup_gain > 0 ? s.starts : s.depth}>
              {t.lineup_gain != null
                ? (t.lineup_gain > 0 ? `starts +${t.lineup_gain}` : "depth")
                : "depth"}
            </Text>
          </View>
          <View style={s.nums}>
            <Text style={s.score}>fa {t.fa_score}</Text>
            <Text style={[s.bid, t.hard_confirm && s.bidHard]}>
              ${t.bid}{t.hard_confirm ? " ‼" : ""}
            </Text>
            {t.heat > 0 && <Text style={s.heat}>heat {t.heat}</Text>}
          </View>
        </View>
      )}
      ListFooterComponent={
        targets.length > 0 ? <Text style={s.footer}>{data.note}</Text> : null
      }
      style={{ backgroundColor: T.ground }}
      contentContainerStyle={{ paddingBottom: 40 }}
    />
  );
}

const s = StyleSheet.create({
  loading: { color: T.inkDim, textAlign: "center", marginTop: 60, letterSpacing: 2 },
  empty: {
    margin: 14, padding: 16, borderWidth: 1, borderColor: T.line,
    borderRadius: 6, backgroundColor: T.panel2,
  },
  emptyText: { color: T.inkDim, lineHeight: 20 },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    paddingHorizontal: 14, paddingVertical: 9,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: T.line,
  },
  rank: { color: T.inkFaint, fontFamily: "monospace", width: 20, fontSize: 12 },
  posDot: { width: 8, height: 8, borderRadius: 2 },
  who: { flex: 1, minWidth: 0 },
  name: { color: T.ink, fontWeight: "600", fontSize: 15 },
  team: { color: T.inkFaint, fontSize: 11, letterSpacing: 0.5, marginTop: 1 },
  starts: { color: T.lampBright, fontSize: 11, marginTop: 1, fontWeight: "600" },
  depth: { color: T.inkFaint, fontSize: 11, marginTop: 1 },
  nums: { alignItems: "flex-end" },
  score: { color: T.ink, fontFamily: "monospace", fontSize: 12 },
  bid: { color: T.brassBright, fontFamily: "monospace", fontSize: 14, fontWeight: "700", marginTop: 2 },
  bidHard: { color: T.marigold },
  heat: { color: T.oxblood, fontFamily: "monospace", fontSize: 10, marginTop: 2 },
  footer: { color: T.inkFaint, padding: 16, lineHeight: 18, fontSize: 12 },
});
