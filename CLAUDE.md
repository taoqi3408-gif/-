# V93000 SMT7 Test Method 开发规范

平台：Advantest V93000 / SmarTest 7 (SMT7)
板卡：MCE，接口：RDI (SmartRDI 2.3.0+)

---

## 1. 标准模板结构

所有测试代码严格按以下模板编写，不得改动框架：

```cpp
#include "testmethod.hpp"

//for test method API interfaces (any other include should be added above this line)
//for MTP test method API interfaces
#include "MtpTest.hpp"
#include "solution_tools.h"
#include "mapi.hpp"
// 如需 RDI，在此加：#include "rdi.hpp"
using namespace std;

/**
 * Test method class.
 *
 * For each testsuite using this test method, one object of this
 * class is created.
 */
class ClassName: public testmethod::TestMethod {

    // 成员变量声明（参数）

protected:
    /**
     * Initialize the parameter interface to the testflow.
     * This method is called just once after a testsuite is created.
     */
    virtual void initialize() {
        //Add your initialization code here
        //Note: Test Method API should not be used in this method!
    }

    /**
     * This test is invoked per site.
     */
    virtual void run() {
        //Add your test code here.
        return;
    }

    /**
     * This function will be invoked once the specified parameter's value is changed.
     * @param parameterIdentifier
     */
    virtual void postParameterChange(const string& parameterIdentifier) {
        //Add your code here
        //Note: Test Method API should not be used in this method!
        return;
    }

    /**
     * This function will be invoked once the Select Test Method Dialog is opened.
     */
    virtual const string getComment() const {
        string comment = " please add your comment for this test method.";
        return comment;
    }
};
REGISTER_TESTMETHOD("GroupName.ClassName", ClassName);
```

---

## 2. initialize() 规则

- 只放 `addParameter().setDefault()` 和 `addLimit()`
- **禁止**使用 Test Method API
- 参数格式：

```cpp
addParameter("ParamName",
             "type",       // int / double / string
             &memberVar,
             testmethod::TM_PARAMETER_INPUT)
    .setDefault("defaultValue");

addLimit("LimitName");
```

---

## 3. run() 内部结构规则

### 3.1 变量声明放最前面

结果数组、flag、testsuite 名等跨块使用的变量，必须在 `run()` 开头声明：

```cpp
INT    iGlobalOverOn = 1;
INT    iDebugFlag    = 1;
INT    iOfflineFlag  = 1;
STRING sTestsuiteName;

GET_TESTFLOW_FLAG("global_overon", &iGlobalOverOn);
GET_TESTFLOW_FLAG("debug_analog",  &iDebugFlag);
GET_SYSTEM_FLAG  ("offline",       &iOfflineFlag);
GET_TESTSUITE_NAME(sTestsuiteName);
```

### 3.2 结果变量必须是数组并提前初始化

多 site 下禁止用标量保存结果（会被覆盖，只剩最后一个 site 的值）：

```cpp
ARRAY_D Result;
Result.resize(GET_SITE_COUNT());
Result = 0.0;
```

### 3.3 ON_FIRST_INVOCATION —— 只放硬件采集

`ON_FIRST_INVOCATION_BEGIN()` ~ `ON_FIRST_INVOCATION_END()` 只执行一次，
**只放**硬件动作：

```cpp
ON_FIRST_INVOCATION_BEGIN();

    CONNECT();
    RDI_BEGIN();
        rdi.func().label("pattern_name").execute();
        rdi.portSync();
        rdi.wait(10 ms);
        // MCE / DC / DGT 采集
    RDI_END();

ON_FIRST_INVOCATION_END();
```

**禁止**在此块内：
- 放 `FOR_EACH_SITE` 循环
- 声明后续外部要用的变量（出了 END 即失效）
- 放 `getValue` / 数据分析 / `judgeAndLog`

### 3.4 FOR_EACH_SITE —— 放在外面做数据分析

数据分析与判断放在 `ON_FIRST_INVOCATION_END()` 之后：

```cpp
FOR_EACH_SITE_BEGIN();

    int site = CURRENT_SITE_NUMBER();

    Result[site] = rdi.id("captureName").getValue(PinName);

    // DSP 计算 / 自定义运算

    TESTSET().cont(true).judgeAndLog_ParametricTest(
        sTestsuiteName, "LimitName", "LimitName", tmLimits, Result[site]);

FOR_EACH_SITE_END();
```

---

## 4. RDI Force/Measure 写法 (SmartRDI 2.3.0+)

先 Force 再 Measure，两步分开 execute：

```cpp
rdi.dc("dcMeas").pin(MeasPin).iForce(1 mA).execute();  // Force
rdi.dc("dcMeas").pin(MeasPin).vMeas().execute();        // Measure
```

> 2.3.0 之前：第二步须写 `iForce(0)`；2.3.0 起直接复用原 force 值。

---

## 5. 各方法 API 使用限制

| 方法 | 可用 Test Method API |
|------|---------------------|
| `initialize()` | ❌ 禁止 |
| `run()` | ✅ 可用 |
| `postParameterChange()` | ❌ 禁止 |
| `getComment()` | ❌ 禁止 |

---

## 6. 执行顺序总结（多 site）

```
run() 开头
├── 变量声明 / flag 读取 / 结果数组初始化

ON_FIRST_INVOCATION（只跑 1 次）
├── CONNECT
└── RDI pattern + MCE 采集触发（所有 site 同时）

FOR_EACH_SITE（每 site 各跑 1 次）
├── getValue 读各自数据
├── DSP / 自定义计算
└── judgeAndLog 判断写 Datalog
```
