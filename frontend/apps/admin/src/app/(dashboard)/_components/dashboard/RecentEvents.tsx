"use client";

import { useCallback, useEffect, useState } from "react";
import { Card, Spin, Timeline, Typography } from "antd";
import { useRouter } from "next/navigation";
import api from "@/lib/api";

const { Text } = Typography;

interface EventItem {
  event_type: string;
  message: string;
  timestamp: string;
  action_url: string | null;
}

const EVENT_COLORS: Record<string, string> = {
  scan_surge: "#1677ff",
  campaign_status_change: "#52c41a",
  channel_anomaly: "#cf1322",
  new_signup: "#722ed1",
  claim_milestone: "#fa8c16",
};

export default function RecentEvents() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const fetchData = useCallback(async () => {
    try {
      const res = await api.get("/analytics/recent-events", {
        params: { limit: 5 },
      });
      setEvents(res.data?.events || []);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  return (
    <Card title="最近动态" size="small" style={{ height: "100%" }}>
      {loading ? (
        <div className="flex items-center justify-center" style={{ height: 150 }}>
          <Spin />
        </div>
      ) : events.length === 0 ? (
        <div className="flex items-center justify-center text-gray-400" style={{ height: 150 }}>
          暂无动态
        </div>
      ) : (
        <Timeline
          items={events.map((event) => ({
            color: EVENT_COLORS[event.event_type] || "gray",
            children: (
              <div
                style={{ cursor: event.action_url ? "pointer" : "default" }}
                onClick={() => {
                  if (event.action_url?.startsWith("/")) {
                    router.push(event.action_url);
                  }
                }}
              >
                <Text>{event.message}</Text>
                <br />
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {new Date(event.timestamp).toLocaleDateString("zh-CN")}
                </Text>
              </div>
            ),
          }))}
        />
      )}
    </Card>
  );
}
