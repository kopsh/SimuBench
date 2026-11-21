## panel 驱动收尾：scope 设置列由 display.panel 出示；SMU 其他设置盒不丢 when 隐藏的设置（Issue 1774，裁决两条）

| 完成条件 | 自检 |
| --- | --- |
| 裁决 1（issue 评论）：scope 屏设置列改由 display.panel 驱动（改写「示波器屏幕自己画」的旧注释——它与 D83 的「一份声明喂检查器、TUI 与 Web」矛盾）；无面板定义回退通用列（#1469 的既有模式） | ✅ `scope._setting_controls` 走 `rows.panel_group_paths`（when 感知）；siglent YAML 注释改写；无面板回退 `{"": 全部 state}`——unit fixture 的 sb-scope-1（无面板模型）由它覆盖，真 siglent（demo 台）由快照覆盖 |
| 裁决 2（issue 评论）：when 隐藏的设置落「其他设置」盒，不再静默不可达；主集合与通道盒按钮同一推导 | ✅ `instrument._other_paths` 主集合改问 `C.panel_layout`（通道盒按钮的同一来源）——初版用 `rows.panel_group_paths` 出现同一设置双盒显示（探针实测），换同源后消失。e2e 新测试 `test_a_when_hidden_role_setting_lands_in_the_other_box`（内联 b2901a 台架：function=VOLTage 时 voltage 角色在通道盒，configure CURRent 后落其他盒；旧代码下红） |
| 可见变化钉进快照基线并披露 | ✅ 两个 scope 快照基线更新（100x32/80x24，panel 驱动的完整设置列）；nets/overview 快照连红经单独重跑与干净 main 对照定性为负载型 settle flake，非本分支 |
| 既有测试存活 | ✅ t2 47 过、units+t3 全过、e2e 新测试过 |
