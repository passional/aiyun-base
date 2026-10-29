import { MenuTypeEnum, MenuThemeEnum } from "@/enums/appEnum";
import { useSettingsStore } from "@stores";
import { MOBILE_BREAKPOINT } from "@/utils/constants/definitions";

/**
 * 设置状态管理
 */
export function useSettingsState() {
  const settingStore = useSettingsStore();

  // 色弱模式初始化
  const initColorWeak = () => {
    if (settingStore.colorWeak) {
      const el = document.getElementsByTagName("html")[0]!;
      setTimeout(() => {
        el.classList.add("color-weak");
      }, 100);
    }
  };

  // 菜单布局切换
  const switchMenuLayouts = (type: MenuTypeEnum) => {
    const isMobile = window.innerWidth < MOBILE_BREAKPOINT;
    if (!isMobile && (type === MenuTypeEnum.LEFT || type === MenuTypeEnum.TOP_LEFT)) {
      settingStore.setMenuOpen(true);
    }
    settingStore.switchMenuLayouts(type);
    if (type === MenuTypeEnum.DUAL_MENU) {
      settingStore.switchMenuStyles(MenuThemeEnum.DESIGN);
      if (!isMobile) settingStore.setMenuOpen(true);
    }
  };

  return {
    // 方法
    initColorWeak,
    switchMenuLayouts,
  };
}
