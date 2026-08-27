import React, { useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { api } from "../api";
import { T } from "../theme";

/** Do actual and optimal start the same men? Slot labels alone are not a
 *  difference worth a second table. */
function sameStarters(card: any): boolean {
  const a = card?.actual ?? [];
  const o = card?.optimal ?? [];
  return a.length === o.length
    && a.every((r: any) => o.some((x: any) => x.id === r.id));
}

export default function ThisWeek() {
  const [card, setCard] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const fetching = useRef(false); // in-flight guard: never stack overlapping polls

  const load = async () => {
    if (fetching.current) return;
    fetching.current = true;
    try {
      setCard(await api.weekCurrent());
    } catch { /* wire indicator handles it */ } finally {
      fetching.current = false;
    }
  };
  useEffect(() => {
    load();
    timer.current = setInterval(load, 3000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, []);

  if (!card) return <Text style={s.loading}>FETCHING THE LINEUP…</Text>;

  // The ready:false payload carries {week, ready, note} and NO lineup arrays —
  // pre-draft, or before the league seats you. Guarding only on `!card` let it
  // through to card.actual.map and white-screened the app on the one state a
  // new user sees first. The web board has always rendered card.note here;
  // this is the same answer in the same words.
  if (!card.ready) {
    return (
      <ScrollView style={{ backgroundColor: T.ground }}
        contentContainerStyle={{ padding: 18, paddingBottom: 48 }}>
        <Text style={s.title}>This Week</Text>
        <Text style={s.rationale}>
          {card.note ?? "No roster on file yet."}
        </Text>
      </ScrollView>
    );
  }

  const rec = card.rec;
  const canAct = rec && ["proposed", "notified", "snoozed"].includes(rec.state);

  const act = (fn: (id: number) => Promise<any>) => async () => {
    if (!rec || busy) return;
    setBusy(true);
    try { await fn(rec.rec_id); } catch { /* surfaced by wire state */ }
    setBusy(false);
    load();
  };

  return (
    <ScrollView style={{ backgroundColor: T.ground }} contentContainerStyle={{ padding: 14, paddingBottom: 48 }}>
      {card.material || (rec && rec.state !== "verified") ? (
        <View style={s.verdict}>
          <Text style={s.title}>
            {card.injury_flag ? "You have trouble in the lineup." : "The room found points."}
          </Text>
          <Text style={s.delta}>{card.delta > 0 ? `+${card.delta}` : card.delta}</Text>
          <Text style={s.rationale}>{rec?.rationale ?? ""}</Text>
          {rec && (
            <Text style={s.state} accessibilityLiveRegion="polite">
              STATE · {rec.state === "dry_run" ? "DRY RUN" : String(rec.state).toUpperCase()}
            </Text>
          )}
          {canAct && (
            <View style={s.actions}>
              <Pressable style={[s.btn, s.btnPrimary]} disabled={busy} onPress={act(api.approve)}
                accessibilityRole="button" accessibilityState={{ disabled: busy }}
                accessibilityLabel="Approve and execute the lineup swap">
                <Text style={s.btnPrimaryText}>{busy ? "WORKING…" : "APPROVE & EXECUTE"}</Text>
              </Pressable>
              <View style={s.row}>
                <Pressable style={[s.btn, s.btnGhost]} disabled={busy} onPress={act(api.snooze)}
                  accessibilityRole="button" accessibilityState={{ disabled: busy }}
                  accessibilityLabel="Snooze this recommendation for thirty minutes">
                  <Text style={s.btnGhostText}>SNOOZE 30M</Text>
                </Pressable>
                <Pressable style={[s.btn, s.btnGhost]} disabled={busy} onPress={act(api.ignore)}
                  accessibilityRole="button" accessibilityState={{ disabled: busy }}
                  accessibilityLabel="Ignore this recommendation">
                  <Text style={s.btnGhostText}>IGNORE</Text>
                </Pressable>
              </View>
            </View>
          )}
          {rec && ["approved", "executed"].includes(rec.state) && (
            <Text style={s.moving} accessibilityLiveRegion="polite">The hands are moving…</Text>
          )}
          {rec && rec.state === "dry_run" && (
            <Text style={s.dryRun}>
              DRY RUN — nothing touched. Set it in Sleeper yourself if you agree.
            </Text>
          )}
        </View>
      ) : (
        <View style={s.allgood}>
          <Text style={s.allgoodText}>
            Lineup optimal — projected {card.actual_total} for week {card.week}.
            {rec?.state === "verified" ? " Last swap verified against the API. ✓" : ""}
          </Text>
        </View>
      )}

      {/* The same eleven men shuffled between slots is not a recommendation: a
          WR in your FLEX and a WR in your WR2 score identically. A second
          table with different-looking rows and an identical total invites the
          reader to go fix something that isn't broken. Mirrors sameStarters()
          on the web board. */}
      {(sameStarters(card) ? (["actual"] as const) : (["actual", "optimal"] as const)).map((key) => (
        <View key={key} style={s.lineup}>
          <Text style={s.lineupTitle}>
            {key === "actual"
              ? (sameStarters(card) ? `YOUR WEEK ${card.week}` : `ACTUAL — WEEK ${card.week}`)
              : "OPTIMAL"}
            {"   "}
            {key === "actual" ? card.actual_total : card.optimal_total}
          </Text>
          {card[key].map((r: any) => (
            <View key={`${key}-${r.slot}-${r.id}`} style={s.prow}>
              <Text style={s.slot}>{r.slot}</Text>
              <View style={[s.posDot, { backgroundColor: T.pos[r.pos] ?? T.inkFaint }]} />
              <Text style={s.pname} numberOfLines={1}>
                {r.name}
                {r.injury ? `  · ${String(r.injury).toUpperCase()}` : r.bye ? "  · BYE" : ""}
              </Text>
              <Text style={s.proj}>{r.proj.toFixed(1)}</Text>
            </View>
          ))}
        </View>
      ))}
    </ScrollView>
  );
}

const s = StyleSheet.create({
  loading: { color: T.inkDim, textAlign: "center", marginTop: 60, letterSpacing: 2 },
  verdict: {
    borderWidth: 1, borderColor: T.brassDeep, borderRadius: 6,
    backgroundColor: T.panel, padding: 16, marginBottom: 16,
  },
  title: { color: T.ink, fontSize: 19, fontWeight: "700" },
  delta: { color: T.brassBright, fontSize: 34, fontWeight: "700", marginTop: 4 },
  rationale: { color: T.inkDim, marginTop: 8, lineHeight: 20 },
  state: { color: T.inkFaint, fontFamily: "monospace", fontSize: 11, letterSpacing: 2, marginTop: 12 },
  actions: { marginTop: 14, gap: 10 },
  row: { flexDirection: "row", gap: 10 },
  btn: { borderRadius: 5, minHeight: 46, alignItems: "center", justifyContent: "center", paddingHorizontal: 16 },
  btnPrimary: { backgroundColor: T.brass },
  btnPrimaryText: { color: T.ground, fontWeight: "700", letterSpacing: 1 },
  btnGhost: { borderWidth: 1, borderColor: T.line, flex: 1 },
  btnGhostText: { color: T.inkDim, fontWeight: "600", letterSpacing: 1 },
  moving: { color: T.brass, marginTop: 12, letterSpacing: 1 },
  dryRun: { color: T.marigold, marginTop: 12, lineHeight: 19 },
  allgood: {
    borderWidth: 1, borderColor: T.lamp, borderRadius: 6,
    backgroundColor: T.panel, padding: 16, marginBottom: 16,
  },
  allgoodText: { color: T.ink, lineHeight: 20 },
  lineup: {
    borderWidth: 1, borderColor: T.line, borderRadius: 6,
    backgroundColor: T.panel, marginBottom: 14, overflow: "hidden",
  },
  lineupTitle: {
    color: T.inkDim, fontFamily: "monospace", fontSize: 12, letterSpacing: 2,
    padding: 12, borderBottomWidth: 1, borderBottomColor: T.line, backgroundColor: T.panel2,
  },
  prow: {
    flexDirection: "row", alignItems: "center", gap: 8,
    paddingHorizontal: 12, paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: T.line,
  },
  slot: { color: T.inkFaint, fontFamily: "monospace", fontSize: 11, width: 38 },
  posDot: { width: 8, height: 8, borderRadius: 2 },
  pname: { color: T.ink, flex: 1, fontWeight: "600" },
  proj: { color: T.ink, fontFamily: "monospace", fontSize: 13 },
});
