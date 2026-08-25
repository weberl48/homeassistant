import { StatusBar } from "expo-status-bar";
import React, { useEffect, useState } from "react";
import { Pressable, SafeAreaView, StyleSheet, Text, View } from "react-native";

import { api } from "./src/api";
import { listenForActions, setUpPush } from "./src/push";
import DraftBoard from "./src/screens/DraftBoard";
import Ledger from "./src/screens/Ledger";
import Parlor from "./src/screens/Parlor";
import ThisWeek from "./src/screens/ThisWeek";
import Waivers from "./src/screens/Waivers";
import { T } from "./src/theme";

type Tab = "board" | "week" | "street" | "parlor" | "ledger";

const TAB_LABEL: Record<Tab, string> = {
  board: "BOARD",
  week: "WEEK",
  street: "STREET",
  parlor: "PARLOR",
  ledger: "LEDGER",
};

export default function App() {
  const [tab, setTab] = useState<Tab>("board");
  const [wire, setWire] = useState<"live" | "down">("down");

  useEffect(() => {
    setUpPush();
    const stop = listenForActions();
    const ping = setInterval(async () => {
      try {
        await api.board();
        setWire("live");
      } catch {
        setWire("down");
      }
    }, 4000);
    return () => {
      stop();
      clearInterval(ping);
    };
  }, []);

  return (
    <SafeAreaView style={s.root}>
      <StatusBar style="light" />
      <View style={s.masthead}>
        <View>
          <Text style={s.wordmark}>BOOTLEGGER</Text>
          <Text style={s.est}>EST. 2026 · THE BACK ROOM</Text>
        </View>
        <Text style={[s.wire, wire === "down" && s.wireDown]}>
          {wire === "live" ? "● WIRE LIVE" : "● WIRE DOWN"}
        </Text>
      </View>
      <View style={s.tabs}>
        {(["board", "week", "street", "parlor", "ledger"] as Tab[]).map((t) => (
          <Pressable
            key={t}
            onPress={() => setTab(t)}
            style={[s.tab, tab === t && s.tabActive]}
          >
            <Text
              style={[s.tabText, tab === t && s.tabTextActive]}
              numberOfLines={1}
              adjustsFontSizeToFit
            >
              {TAB_LABEL[t]}
            </Text>
          </Pressable>
        ))}
      </View>
      {tab === "board" ? <DraftBoard />
        : tab === "week" ? <ThisWeek />
        : tab === "street" ? <Waivers />
        : tab === "parlor" ? <Parlor />
        : <Ledger />}
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: T.ground },
  masthead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-end",
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: T.line,
  },
  wordmark: { color: T.brassBright, fontSize: 24, letterSpacing: 4, fontWeight: "700" },
  est: { color: T.inkFaint, fontSize: 10, letterSpacing: 2, marginTop: 2 },
  wire: { color: T.lamp, fontSize: 11, letterSpacing: 1 },
  wireDown: { color: T.oxblood },
  tabs: { flexDirection: "row", borderBottomWidth: 1, borderBottomColor: T.line },
  tab: { flex: 1, paddingVertical: 12, paddingHorizontal: 2, alignItems: "center" },
  tabActive: { borderBottomWidth: 2, borderBottomColor: T.brass },
  tabText: { color: T.inkDim, fontSize: 11, letterSpacing: 0.8, fontWeight: "600" },
  tabTextActive: { color: T.brassBright },
});
