/** Push wiring (design doc §2 [push]): two Android channels and shade action
 * buttons. [Approve & Execute] fires the actuation job without opening the app. */
import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import { api } from "./api";

export const CATEGORY_REC = "lineup-rec";

export async function setUpPush(): Promise<string | null> {
  await Notifications.setNotificationCategoryAsync(CATEGORY_REC, [
    { identifier: "approve", buttonTitle: "Approve & Execute" },
    { identifier: "snooze", buttonTitle: "Snooze 30m" },
  ]);
  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("recommendations", {
      name: "Recommendations",
      importance: Notifications.AndroidImportance.DEFAULT,
    });
    await Notifications.setNotificationChannelAsync("game-time-emergency", {
      name: "Game-time emergency",
      importance: Notifications.AndroidImportance.MAX,
      sound: "default",
      bypassDnd: true, // needs the one-time DND-access grant in Android settings
      vibrationPattern: [0, 400, 200, 400],
    });
  }
  if (!Device.isDevice) return null;
  const perm = await Notifications.requestPermissionsAsync();
  if (perm.status !== "granted") return null;
  const token = (await Notifications.getExpoPushTokenAsync()).data;
  try {
    await api.registerDevice(token);
  } catch {
    // API unreachable (Tailscale down?) — the app retries on next launch.
  }
  return token;
}

/** Handle shade action taps; returns an unsubscribe function. */
export function listenForActions(): () => void {
  const sub = Notifications.addNotificationResponseReceivedListener(async (resp) => {
    const recId = resp.notification.request.content.data?.rec_id as number | undefined;
    if (!recId) return;
    try {
      if (resp.actionIdentifier === "approve") await api.approve(recId);
      else if (resp.actionIdentifier === "snooze") await api.snooze(recId);
    } catch {
      // Approval queued path failed — the app's This Week screen still works,
      // and the push itself deep-links there.
    }
  });
  return () => sub.remove();
}
