# 项目说明

本仓库用于 Advantest V93000 / SmarTest 7 (SMT7) ATE 测试程序开发。
平台：V93000，板卡：MCE，接口：RDI (SmartRDI 2.3.0+)。

---

## V93000 SMT7 Test Method 编写规范

### 1. 整体结构

每个 Test Method 继承 `testmethod::TestMethod`，包含：

- `initialize()`：用 `addParameter().setDefault()` 声明 Testflow 可配置参数；用 `addLimit()` 声明 limit 项。**此方法内不可使用 Test Method API。**
- `run()`：每 site 执行的测试主体。
- `postParameterChange()`：参数变更回调，**不可使用 Test Method API**。
- `getComment()`：返回 Test Method 注释。
- 文件末尾用 `REGISTER_TESTMETHOD("Group.ClassName", ClassName);` 注册。

### 2. ON_FIRST_INVOCATION —— 只放硬件采集

`ON_FIRST_INVOCATION_BEGIN()` ~ `ON_FIRST_INVOCATION_END()` 之间**只执行一次**，
所有 site 同时进行硬件动作。这里**只放**：

- `CONNECT()`
- `RDI_BEGIN()` ... `RDI_END()`：func pattern、portSync、wait、MCE/DC/DGT 采集

**禁止**在此块内：
- 放 `FOR_EACH_SITE` 循环（硬件采集对所有 site 一次完成，无需循环）
- 声明后面外部还要用的变量（块作用域，出了 END 即失效）
- 放 `getValue` / 数据分析 / `judgeAndLog`

### 3. FOR_EACH_SITE —— 放在外面做数据分析

数据分析与判断放在 `ON_FIRST_INVOCATION_END()` **之后**，用
`FOR_EACH_SITE_BEGIN()` ~ `FOR_EACH_SITE_END()` 逐 site 处理：

- `getValue` 读取各 site 数据
- DSP 计算 / 自定义运算
- `TESTSET().judgeAndLog_ParametricTest(...)` 判断并写 Datalog

### 4. 结果变量必须是按 site 索引的数组，且提前初始化

多 site 下严禁用标量保存结果（会被逐 site 覆盖，只剩最后一个 site）。

- 用 `ARRAY_D`（或对应 site-aware 容器）
- 在 `run()` 开头 `resize(GET_SITE_COUNT())` 并清零
- 按 `Result[site]` 下标存取

### 5. 变量作用域规则

- 结果数组、flag、testsuite 名等**后续要跨块使用的变量**，在 `run()` 开头声明。
- `ON_FIRST_INVOCATION` 块内声明的变量出块即销毁，不要依赖。

### 6. RDI Force/Measure 写法 (SmartRDI 2.3.0+)

先 Force 再 Measure，两步分开 execute：

```cpp
rdi.dc("dcMeas").pin(MeasPin).iForce(1 mA).execute();   // Force
rdi.dc("dcMeas").pin(MeasPin).vMeas().execute();        // Measure
```

注意：2.3.0 之前的版本，第二步 Measure 里的 force 须写 `iForce(0)`；2.3.0 起直接复用 `iForce(1 mA)`。

---

## 标准模板

```cpp
#include "testmethod.hpp"
//for test method API interfaces (any other include should be added above this line)
//for MTP test method API interfaces
#include "MtpTest.hpp"
#include "solution_tools.h"
#include "mapi.hpp"
#include "rdi.hpp"
using namespace std;

class MEAS_DC_RDI: public testmethod::TestMethod {

    int    BinNum;
    string MeasPin;
    double ForceValue;

protected:
    virtual void initialize() {
        addParameter("BinNum", "int", &BinNum,
                     testmethod::TM_PARAMETER_INPUT).setDefault("113");
        addParameter("MeasPin", "string", &MeasPin,
                     testmethod::TM_PARAMETER_INPUT).setDefault("VOUT");
        addParameter("ForceValue", "double", &ForceValue,
                     testmethod::TM_PARAMETER_INPUT).setDefault("1");
        addLimit("VOUT");
    }

    virtual void run() {

        INT    iDebugFlag = 1;
        STRING sTestsuiteName;
        GET_TESTFLOW_FLAG("debug_analog", &iDebugFlag);
        GET_TESTSUITE_NAME(sTestsuiteName);

        // 结果数组：按 site 数分配并清零
        ARRAY_D Result_DC;
        Result_DC.resize(GET_SITE_COUNT());
        Result_DC = 0.0;

        RDI_INIT();

        // 只执行一次：硬件采集，所有 site 同时进行，不放 site loop
        ON_FIRST_INVOCATION_BEGIN();
            CONNECT();
            RDI_BEGIN();
                rdi.func().label("fc_setup_dc_meas").execute();
                rdi.portSync();
                rdi.wait(10 ms);
                rdi.dc("dcMeas").pin(MeasPin).iForce(ForceValue mA).execute();
                rdi.dc("dcMeas").pin(MeasPin).vMeas().execute();
            RDI_END();
        ON_FIRST_INVOCATION_END();

        // 外面逐 site 分析数据 / 判断
        FOR_EACH_SITE_BEGIN();
            int site = CURRENT_SITE_NUMBER();
            Result_DC[site] = rdi.id("dcMeas").getValue(MeasPin);
            if (iDebugFlag) {
                cout << "Site " << site
                     << " Voltage (V) = " << Result_DC[site] << endl;
            }
            TESTSET().cont(true).judgeAndLog_ParametricTest(
                sTestsuiteName, "VOUT", "VOUT", tmLimits, Result_DC[site]);
        FOR_EACH_SITE_END();

        return;
    }

    virtual void postParameterChange(const string& parameterIdentifier) {
        return;
    }

    virtual const string getComment() const {
        return " please add your comment for this test method.";
    }
};
REGISTER_TESTMETHOD("MEAS_DC.MEAS_DC_RDI", MEAS_DC_RDI);
```

> 注：`FOR_EACH_SITE_BEGIN/END`、`CURRENT_SITE_NUMBER()`、`GET_SITE_COUNT()` 等宏的确切名称可能因 SMT7 小版本而异，按项目内现有 TM 写法对齐；但分层原则（硬件采集进 ON_FIRST_INVOCATION、数据分析进 FOR_EACH_SITE、结果用 site 数组并初始化）不变。
