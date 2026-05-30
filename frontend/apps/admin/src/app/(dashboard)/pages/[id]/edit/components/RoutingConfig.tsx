"use client";

import { Button, DatePicker, Input, Select, Space, Switch, Tag, Typography } from "antd";
import { PlusOutlined } from "@ant-design/icons";
import dayjs from "dayjs";
import type { PageDSL, CampaignPeriod } from "@/lib/page-dsl";

const { Text } = Typography;

export function RoutingConfig({
  dsl,
  onChange,
}: {
  dsl: PageDSL;
  onChange: (dsl: PageDSL) => void;
}) {
  const routing = dsl.routing;
  const periods = routing?.campaign_periods ?? [];

  const updateRouting = (updates: Partial<PageDSL["routing"]>) => {
    const newRouting = { ...routing, ...updates };
    onChange({ ...dsl, routing: newRouting });
  };

  const addPeriod = () => {
    const newPeriods: CampaignPeriod[] = [
      { campaign_id: "", start_at: "", end_at: "", mode: "campaign" },
      ...periods,
    ];
    updateRouting({ campaign_periods: newPeriods });
  };

  const updatePeriod = (index: number, updates: Record<string, unknown>) => {
    const newPeriods = periods.map((p, i) => (i === index ? { ...p, ...updates } : p));
    updateRouting({ campaign_periods: newPeriods });
  };

  const removePeriod = (index: number) => {
    updateRouting({ campaign_periods: periods.filter((_, i) => i !== index) });
  };

  return (
    <div>
      <div className="mb-3">
        <div className="mb-2 flex items-center gap-2">
          <Switch
            size="small"
            checked={routing?.default_page ?? true}
            onChange={(checked) => updateRouting({ default_page: checked })}
          />
          <Text>设为默认页面</Text>
        </div>
      </div>

      <div className="mb-2 flex items-center justify-between">
        <Text strong>活动期配置</Text>
        <Button size="small" icon={<PlusOutlined />} onClick={addPeriod}>
          添加活动期
        </Button>
      </div>

      <div className="space-y-3">
        {periods.map((period, i) => (
          <div key={i} className="rounded border p-3">
            <div className="flex items-center gap-2 mb-2">
              <Tag color={period.mode === "evergreen" ? "green" : "blue"}>
                {period.mode === "evergreen" ? "常驻" : "活动期"}
              </Tag>
              <Select
                size="small"
                value={period.mode}
                onChange={(mode) => updatePeriod(i, { mode })}
                options={[
                  { value: "evergreen", label: "常驻（非活动期）" },
                  { value: "campaign", label: "活动期" },
                ]}
                style={{ width: 160 }}
              />
              <div className="flex-1" />
              <Button size="small" danger onClick={() => removePeriod(i)}>
                删除
              </Button>
            </div>
            {period.mode === "campaign" && (
              <div className="space-y-2">
                <Input
                  size="small"
                  placeholder="活动 ID（可选）"
                  value={period.campaign_id || ""}
                  onChange={(e) => updatePeriod(i, { campaign_id: e.target.value })}
                />
                <Space>
                  <DatePicker
                    size="small"
                    placeholder="开始时间"
                    value={period.start_at ? dayjs(period.start_at) : undefined}
                    onChange={(d) => updatePeriod(i, { start_at: d?.toISOString() || "" })}
                  />
                  <DatePicker
                    size="small"
                    placeholder="结束时间"
                    value={period.end_at ? dayjs(period.end_at) : undefined}
                    onChange={(d) => updatePeriod(i, { end_at: d?.toISOString() || "" })}
                  />
                </Space>
              </div>
            )}
          </div>
        ))}
        {periods.length === 0 && (
          <div className="py-8 text-center text-gray-400">暂无活动期配置</div>
        )}
      </div>
    </div>
  );
}