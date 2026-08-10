import { useState } from 'react';

/** worker 规范城市名，顺序固定；「全国」在 wukong 模式无映射，刻意不提供。 */
export const REGION_OPTIONS = [
  '北京',
  '上海',
  '天津',
  '重庆',
  '广州',
  '深圳',
  '杭州',
  '南京',
  '武汉',
  '成都',
  '西安',
  '长沙',
  '郑州',
  '济南',
  '合肥',
  '福州',
  '南昌',
  '石家庄',
  '太原',
  '呼和浩特',
  '沈阳',
  '长春',
  '哈尔滨',
  '南宁',
  '海口',
  '贵阳',
  '昆明',
  '拉萨',
  '兰州',
  '西宁',
  '银川',
  '乌鲁木齐',
] as const;

export const DEFAULT_REGIONS = ['北京', '上海'];

type Props = {
  value?: string[];
  onChange?: (next: string[]) => void;
  defaultValue?: string[];
  disabled?: boolean;
};

export function RegionMultiSelect({ value, onChange, defaultValue, disabled = false }: Props) {
  const [inner, setInner] = useState<string[]>(defaultValue ?? DEFAULT_REGIONS);
  const selected = value ?? inner;

  function toggle(city: string) {
    const next = selected.includes(city)
      ? selected.filter((item) => item !== city)
      : [...selected, city];
    if (value === undefined) setInner(next);
    onChange?.(next);
  }

  return (
    <div className="region-multi-select" role="group" aria-label="地域多选">
      {REGION_OPTIONS.map((city) => {
        const active = selected.includes(city);
        return (
          <button
            key={city}
            type="button"
            className={`region-chip${active ? ' active' : ''}`}
            aria-pressed={active}
            disabled={disabled}
            onClick={() => toggle(city)}
          >
            {city}
          </button>
        );
      })}
    </div>
  );
}
