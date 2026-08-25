import React, { useEffect, useRef, useState } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { api } from "../api";
import { T } from "../theme";

type Rule = { rule_id: number; name: string; threshold: number | null; enabled: number };
type ActionRow = {
  action_id: number;
  rec_id: number | null;
  step: string;
  ts: string;
  kind?: string | null;
  week?: number | null;
};

export default function Ledger() {
  const [rules, setRules] = useState<Rule[] | null>(null);
  const [audit, setAudit] = useState<ActionRow[] | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [r, a] = await Promise.all([api.rules(), api.audit()]);
        setRules(r);
        setAudit(a);
      } catch {
        /* wire indicator in App handles it */
      }
    };
    load();
    timer.current = setInterval(load, 8000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, []);

  if (!rules || !audit) return <Text style={s.loading}>OPENING THE LEDGER…</Text>;

  return (
    <ScrollView style={{ backgroundColor: T.ground }} contentContainerStyle={{ padding: 14, paddingBottom: 48 }}>
      <Text style={s.section}>RULES · READ-ONLY</Text>
      <View style={s.card}>
        {rules.length === 0 ? (
          <Text style={s.emptyText}>No rules configured.</Text>
        ) : (
          rules.map((r, i) => (
            <View key={r.rule_id} style={[s.ruleRow, i === rules.length - 1 && s.rowLast]}>
              <Text style={s.ruleName}>{r.name.replace(/_/g, " ")}</Text>
              <Text style={[s.ruleState, r.enabled ? s.ruleOn : s.ruleOff]}>
                {r.enabled ? "ON" : "OFF"}
              </Text>
            </View>
          ))
        )}
      </View>

      <Text style={s.section}>LIFECYCLE</Text>
      <View style={s.card}>
        {audit.length === 0 ? (
          <Text style={s.emptyText}>Nothing logged yet.</Text>
        ) : (
          audit.map((a, i) => (
            <View key={a.action_id} style={[s.auditRow, i === audit.length - 1 && s.rowLast]}>
              <Text style={s.auditStep} numberOfLines={1}>{a.step}</Text>
              <Text style={s.auditMeta}>
                {a.kind ? `${String(a.kind).toUpperCase()} · ` : ""}
                {a.week != null ? `wk ${a.week} · ` : ""}
                {a.ts}
              </Text>
            </View>
          ))
        )}
      </View>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  loading: { color: T.inkDim, textAlign: "center", marginTop: 60, letterSpacing: 2 },
  section: { color: T.inkFaint, fontSize: 11, letterSpacing: 2, fontWeight: "700", marginBottom: 8, marginTop: 4 },
  card: {
    borderWidth: 1, borderColor: T.line, borderRadius: 6,
    backgroundColor: T.panel, marginBottom: 20, overflow: "hidden",
  },
  emptyText: { color: T.inkDim, padding: 14, lineHeight: 20 },
  ruleRow: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    paddingHorizontal: 14, paddingVertical: 11,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: T.line,
  },
  ruleName: { color: T.ink, fontSize: 14, textTransform: "capitalize", flex: 1, marginRight: 10 },
  ruleState: { fontFamily: "monospace", fontSize: 12, fontWeight: "700", letterSpacing: 1 },
  ruleOn: { color: T.lampBright },
  ruleOff: { color: T.inkFaint },
  auditRow: {
    paddingHorizontal: 14, paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: T.line,
  },
  auditStep: { color: T.ink, fontFamily: "monospace", fontSize: 13, fontWeight: "600" },
  auditMeta: { color: T.inkFaint, fontSize: 11, marginTop: 2 },
  rowLast: { borderBottomWidth: 0 },
});
