import React, { useEffect, useRef, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { api } from "../api";
import { T } from "../theme";

type TradePlayer = { id: string; name: string; pos: string };
type Trade = {
  score: number;
  partner: string;
  partner_roster_id: number;
  give: TradePlayer[];
  receive: TradePlayer[];
  my_gain: number;
  their_gain: number;
  summary: string;
};

function fmtGain(n: number): string {
  return n > 0 ? `+${n}` : `${n}`;
}

export default function Parlor() {
  const [data, setData] = useState<any>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const fetching = useRef(false); // in-flight guard: never stack overlapping polls

  useEffect(() => {
    const load = async () => {
      if (fetching.current) return;
      fetching.current = true;
      try {
        setData(await api.trades());
      } catch {
        /* wire indicator in App handles it */
      } finally {
        fetching.current = false;
      }
    };
    load();
    // Mirrors the web board's trade-scan cadence: it's a full-league scan,
    // so 60s keeps it off the Pi's back.
    timer.current = setInterval(load, 60000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, []);

  if (!data) return <Text style={s.loading}>WORKING THE ROOM…</Text>;

  const trades: Trade[] = data.trades ?? [];

  return (
    <ScrollView style={{ backgroundColor: T.ground }} contentContainerStyle={{ padding: 14, paddingBottom: 48 }}>
      {trades.length === 0 ? (
        <View style={s.empty}>
          <Text style={s.emptyText}>{data.note}</Text>
        </View>
      ) : (
        trades.map((t, i) => (
          <View key={`${t.partner_roster_id}-${i}`} style={s.card}>
            <Text style={s.partner}>WITH {String(t.partner).toUpperCase()}</Text>
            <View style={s.sides}>
              <View style={s.side}>
                <Text style={s.sideLabel}>YOU SEND</Text>
                {t.give.map((p) => (
                  <View key={p.id} style={s.prow}>
                    <View style={[s.posDot, { backgroundColor: T.pos[p.pos] ?? T.inkFaint }]} />
                    <Text style={s.pname} numberOfLines={1}>{p.name}</Text>
                  </View>
                ))}
              </View>
              <View style={s.side}>
                <Text style={s.sideLabel}>YOU GET</Text>
                {t.receive.map((p) => (
                  <View key={p.id} style={s.prow}>
                    <View style={[s.posDot, { backgroundColor: T.pos[p.pos] ?? T.inkFaint }]} />
                    <Text style={s.pname} numberOfLines={1}>{p.name}</Text>
                  </View>
                ))}
              </View>
            </View>
            <View style={s.gains}>
              <Text style={s.gainLabel}>
                your gain{" "}
                <Text style={[s.gainNum, t.my_gain < 0 && s.gainNeg]}>{fmtGain(t.my_gain)}</Text>
              </Text>
              <Text style={s.gainLabel}>
                their gain{" "}
                <Text style={[s.gainNum, t.their_gain < 0 && s.gainNeg]}>{fmtGain(t.their_gain)}</Text>
              </Text>
            </View>
            <Text style={s.summary}>{t.summary}</Text>
          </View>
        ))
      )}
      {trades.length > 0 && <Text style={s.footer}>{data.note}</Text>}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  loading: { color: T.inkDim, textAlign: "center", marginTop: 60, letterSpacing: 2 },
  empty: {
    padding: 16, borderWidth: 1, borderColor: T.line,
    borderRadius: 6, backgroundColor: T.panel2,
  },
  emptyText: { color: T.inkDim, lineHeight: 20 },
  card: {
    borderWidth: 1, borderColor: T.line, borderRadius: 6,
    backgroundColor: T.panel, padding: 14, marginBottom: 12,
  },
  partner: { color: T.brass, fontSize: 11, letterSpacing: 2, fontWeight: "700" },
  sides: { flexDirection: "row", gap: 12, marginTop: 10 },
  side: { flex: 1, backgroundColor: T.panel2, borderRadius: 5, padding: 10 },
  sideLabel: { color: T.inkFaint, fontSize: 10, letterSpacing: 1.5, marginBottom: 6 },
  prow: { flexDirection: "row", alignItems: "center", gap: 6, paddingVertical: 3 },
  posDot: { width: 7, height: 7, borderRadius: 2 },
  pname: { color: T.ink, fontSize: 13, fontWeight: "600", flex: 1 },
  gains: { flexDirection: "row", gap: 18, marginTop: 12 },
  gainLabel: { color: T.inkDim, fontSize: 12 },
  gainNum: { color: T.lampBright, fontFamily: "monospace", fontWeight: "700" },
  gainNeg: { color: T.oxblood },
  summary: { color: T.inkDim, marginTop: 8, lineHeight: 19 },
  footer: { color: T.inkFaint, paddingTop: 4, lineHeight: 18, fontSize: 12 },
});
