"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Card, Empty, Spin, Timeline, Typography } from "antd";
import { useRouter } from "next/navigation";
import api from "@/lib/api";
import { formatAuditAction } from "@/lib/audit";

const { Text } = Typography;

interface EventItem {
  event_type: string;
  message: string;
  timestamp: string;
  action_url: string | null;
}

interface RecentAuditLog {
  id: string;
  action: string;
  resource: string;
  timestamp: string;
  operator: { name: string };
  details?: { resource_name?: string } | null;
}

const EVENT_COLORS: Record<string, string> = {
  scan_surge: "var(--ymt-color-action-primary)",
  campaign_status_change: "var(--ymt-color-feedback-success)",
  channel_anomaly: "var(--ymt-color-feedback-danger)",
  new_signup: "var(--ymt-color-brand-primary)",
  claim_milestone: "var(--ymt-color-action-accent)",
};

export default function RecentEvents() {
  const [events, setEvents] = useState<EventItem[]>([]);
  const [auditLogs, setAuditLogs] = useState<RecentAuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const [isAdmin] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      const stored = window.localStorage.getItem("auth_store");
      return stored ? JSON.parse(stored).role === "admin" : false;
    } catch {
      return false;
    }
  });

  const fetchData = useCallback(async () => {
    try {
      if (isAdmin) {
        const res = await api.get("/audit-logs", {
          params: { page: 1, page_size: 5 },
        });
        setAuditLogs(res.data?.items || []);
      } else {
        const res = await api.get("/analytics/recent-events", {
          params: { limit: 5 },
        });
        setEvents(res.data?.events || []);
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  return (
    <Card
      title={isAdmin ? "最近操作" : "最近动态"}
      size="small"
      style={{ height: "100%" }}
      extra={
        isAdmin ? (
          <Button
            type="link"
            size="small"
            onClick={() => router.push("/settings/audit-logs")}
          >
            查看全部
          </Button>
        ) : null
      }
    >
      {loading ? (
        <div
          className="flex items-center justify-center"
          style={{ height: 150 }}
        >
          <Spin />
        </div>
      ) : (isAdmin ? auditLogs : events).length === 0 ? (
        <div
          className="flex items-center justify-center"
          style={{ minHeight: 150 }}
        >
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={isAdmin ? "暂无操作记录" : "暂无动态"}
          />
        </div>
      ) : (
        <Timeline
          items={
            isAdmin
              ? auditLogs.map((log) => ({
                  color:
                    log.action.includes("deleted") ||
                    log.action.includes("disabled")
                      ? "red"
                      : "blue",
                  content: (
                    <div>
                      <Text>
                        {log.operator.name} · {formatAuditAction(log.action)} ·{" "}
                        {log.details?.resource_name ||
                          log.resource.split(":", 2)[1] ||
                          log.resource}
                      </Text>
                      <br />
                      <Text type="secondary" style={{ fontSize: 11 }}>
                        {new Date(log.timestamp).toLocaleString("zh-CN")}
                      </Text>
                    </div>
                  ),
                }))
              : events.map((event) => ({
                  color: EVENT_COLORS[event.event_type] || "gray",
                  content: (
                    <div
                      style={{
                        cursor: event.action_url ? "pointer" : "default",
                      }}
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
                }))
          }
        />
      )}
    </Card>
  );
}
