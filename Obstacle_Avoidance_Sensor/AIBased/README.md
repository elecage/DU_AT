# 장애물 탐지 바닥센서

## 개요
  * 휠체어 장착이 가능한 전방 장애물 탐지 센서 개발

## 단축어
|단축어 | 의미 |
|-|-|
| **OAS** | Obstacle Avoidance Sensor. 장애물 회피 센서 | 
| **REQ** | Request. 요구사항 | 
| **SON** | Sonar. 초음파 센서 |
| **CAM** | Camera. 카메라 |
| **FUNC** | Function. 기능 |
| **TBD** | To Be Determined. 추후 결정 |

## 시스템 요구사항

| 식별번호 | 요구사항 |
|---|---|
| **OAS-REQ-01** | 장애물 탐지 바닥센서는 휠체어의 후면에 탈부착 가능해야 한다. |
| **OAS-REQ-02** | 장애물 탐지 바닥센서는 휠체어의 후면에 존재하는 장애물을 탐지할 수 있어야 한다. |
| **OAS-REQ-03** | 장애물 탐지 바닥센서는 휠체어의 후면에 장애물이 탐지되었을 때 사용자에게 알림 신호를 통해 장애물의 존재를 알릴 수 있어야 한다. |
| **OAS-REQ-04** | 장애물 탐지 바닥센서는 휠체어와 장애물 간의 거리를 단계별로 구분하여 알림의 형태를 변경할 수 있어야 한다. |
| **OAS-REQ-05** | 장애물 탐지 바닥센서는 전용의 전원을 이용하여 동작할 수 있어야 한다. |
| **OAS-REQ-06** | 장애물 탐지 바닥센서는 1일 *TBD* 시간 이상의 사용이 가능해야 한다. |
| **OAS-REQ-07** | 장애물 탐지 바닥센서는 오염 방지, 생활방수가 가능해야 한다. |


## 보조기기
### 초음파 이용 장애물 탐지
#### 기능
| 식별번호 | 요구사항 추적 | 항목명 | 설명 |
|-|-|-|-|
| **OAS-SON-FUNC-01** | OAS-REQ-01 | 센서 부착 기능 | |
| **OAS-SON-FUNC-02** | OAS-REQ-02 | 장애물 거리 측정 기능 | 초음파 센서를 이용하여 거리 탐지가 가능하다. |
| **OAS-SON-FUNC-03** | OAS-REQ-02 | 장애물 방향 탐지 기능 | 초음파 센서를 이용하여 장애물의 방향을 좌, 우, 중앙으로 구분할 수 있다. |
| **OAS-SON-FUNC-04** | OAS-REQ-03 | 장애물 거리 알람 기능 | 초음파 센서에 측정된 거리에 대해 *TBD* meter

#### 비기능
|식별번호 | 요구사항 추적 | 항목명 | 설명 | 
|-|-|-|-|
| **OAS-SON-NOF-01** | OAS-REQ-05 | 전원 | 5V, *TBD*A 정격 직류 배터리 전원을 인가한다. |
| **OAS-SON-NOF-02** | OAS-REQ-06 | 배터리 용량 | 배터리 용량은 *TBD*Ah 이상이다. |
| **OAS-SON-NOF-03** | OAS-REQ-05 | 충전 | USB 충전 어댑터를 이용하여 배터리를 충전한다.|
| **OAS-SON-NOF-04** | OAS-REQ-06 | | |
#### Bill of Materials
| No. | 부품명 | 수량 | 비고 |
|-|-|-|-|
| 1 | 아두이노 우노(Arduino UNO) | 1 | |
| 2 | 초음파 센서  | 2 | |
| 3 | 스피커 | 2 | 3W, $\phi$ 20|
| 4 | 앰프 모듈 | 1 | 3W, 스테레오 |
|

### 카메라 이용 장애물 탐지

#### 영상 AI 적용 장애물 탐지
