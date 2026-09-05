"use client";

import { useCallback, useEffect, useState } from "react";
import { Alert, message, Space, Spin } from "antd";
import { useRouter } from "next/navigation";
import api, { extractErrorMessage } from "@/lib/api";

interface AlertItem {
  type: string;
  level: string;
  message: string;
  action_url: string;
}

export default function StatusAlerts() {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const fetchAlerts = useCallback(async () => {
    try {
      const res = await api.get("/analytics/alerts");
      setAlerts(res.data?.alerts || []);
    } catch (err) {
      // 加载失败时给出错误提示，保留空态展示以区分“暂无数据”
      message.error(extractErrorMessage(err, "数据加载失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchAlerts();
  }, [fetchAlerts]);

  if (loading) return <Spin size="small" />;
  if (alerts.length === 0) return null;

  const levelToType: Record<string, "error" | "warning" | "info"> = {
    error: "error",
    warning: "warning",
    info: "info",
  };

  return (
    <Space orientation="vertical" className="w-full mb-4">
      {alerts.map((alert, idx) => (
        <Alert
          key={idx}
          type={levelToType[alert.level] || "info"}
          message={alert.message}
          showIcon
          closable
          style={{ cursor: "pointer" }}
          onClick={() => {
            if (alert.action_url.startsWith("/")) {
              router.push(alert.action_url);
            }
          }}
        />
      ))}
    </Space>
  );
}
