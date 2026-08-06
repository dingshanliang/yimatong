"use client";

import { App, Button, Card, Empty, Space, Spin, Typography } from "antd";
import { ReloadOutlined } from "@ant-design/icons";
import { useCallback, useEffect, useState } from "react";

import api, { extractErrorMessage } from "@/lib/api";

import MilestoneTimeline from "./_components/MilestoneTimeline";
import RetrospectiveCard from "./_components/RetrospectiveCard";
import type {
  MilestoneTimelineResponse,
  RetrospectiveRead,
} from "./_components/types";

const { Title, Paragraph } = Typography;

/** 试点复盘页（beads: yimatong-bgag.5，PRD §4.5/§5）。
 * 品牌方见本租户里程碑 + 各期复盘；代运营在客户上下文中见该客户视图。
 */
export default function PilotPage() {
  const { message } = App.useApp();
  const [timeline, setTimeline] = useState<MilestoneTimelineResponse | null>(
    null
  );
  const [retros, setRetros] = useState<RetrospectiveRead[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [tl, rs] = await Promise.all([
        api.get<MilestoneTimelineResponse>("/pilot-milestones"),
        api.get<RetrospectiveRead[]>("/retrospectives"),
      ]);
      setTimeline(tl.data ?? null);
      setRetros(rs.data ?? []);
    } catch (err) {
      message.error(extractErrorMessage(err, "加载试点数据失败"));
      setTimeline(null);
      setRetros([]);
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  if (loading) {
    return (
      <Card>
        <Spin />
      </Card>
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card size="small">
        <Title level={4} style={{ margin: 0 }}>
          试点复盘
        </Title>
        <Paragraph type="secondary" style={{ margin: "4px 0 0" }}>
          上线后第 7/14/30 天的结构化复盘：里程碑时间线、运营
          scorecard、问题与动作追踪。
        </Paragraph>
        <Button
          icon={<ReloadOutlined />}
          size="small"
          onClick={() => void fetchData()}
          style={{ marginTop: 8 }}
        >
          刷新
        </Button>
      </Card>

      {timeline ? (
        <MilestoneTimeline
          milestones={timeline.milestones}
          derivedDurations={timeline.derived_durations}
        />
      ) : (
        <Empty description="暂无里程碑数据" />
      )}

      <div>
        {retros.length === 0 ? (
          <Card title="复盘记录" size="small">
            <Empty description="暂无到期复盘（上线后第 7/14/30 天自动生成）" />
          </Card>
        ) : (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            {retros.map((r) => (
              <RetrospectiveCard
                key={r.id}
                retro={r}
                onChanged={() => void fetchData()}
              />
            ))}
          </Space>
        )}
      </div>
    </Space>
  );
}
