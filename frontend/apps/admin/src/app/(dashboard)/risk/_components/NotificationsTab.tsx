"use client";

import api from "@/lib/api";
import { useCrud } from "@/lib/hooks";
import { Badge, Button, Card, Empty, List, Tag, message } from "antd";

type RiskNotification = Record<string, unknown> & {
  id: string;
  notification_type: string;
  title: string;
  detail: string;
  read: boolean;
};

export function NotificationsTab() {
  const { items, total, page, loading, setPage, mutate } = useCrud<RiskNotification>(
    "/risk-notifications",
  );

  const markRead = async (id: string) => {
    await api.post(`/risk-notifications/${id}/read`);
    mutate();
  };

  const markAllRead = async () => {
    await api.post("/risk-notifications/mark-all-read");
    message.success("已全部标记为已读");
    mutate();
  };

  const unreadCount = items.filter((n) => !n.read).length;

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <span className="text-sm text-text-muted">
          未读 <Badge count={unreadCount} /> / 共 {total} 条
        </span>
        <Button size="small" disabled={unreadCount === 0} onClick={markAllRead}>
          全部已读
        </Button>
      </div>
      <List
        loading={loading}
        dataSource={items}
        locale={{ emptyText: <Empty description="暂无风控通知" /> }}
        pagination={{
          current: page,
          total,
          pageSize: 20,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
        renderItem={(item) => (
          <Card
            size="small"
            className={`mb-2 ${!item.read ? "bg-blue-50 border-blue-200" : ""}`}
            onClick={() => !item.read && markRead(item.id)}
            style={{ cursor: item.read ? "default" : "pointer" }}
          >
            <div className="flex items-start justify-between">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <Tag color={item.notification_type === "risk_block" ? "red" : "orange"}>
                    {item.notification_type === "risk_block" ? "阻断" : "预警"}
                  </Tag>
                  <span className="font-medium">{item.title}</span>
                  {!item.read && <Badge status="processing" />}
                </div>
                <p className="text-sm text-text-muted whitespace-pre-line">{item.detail}</p>
              </div>
            </div>
          </Card>
        )}
      />
    </div>
  );
}
