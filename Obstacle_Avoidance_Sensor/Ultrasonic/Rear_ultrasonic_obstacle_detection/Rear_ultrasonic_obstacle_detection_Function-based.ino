const int trigPin = 13;  // Trigger 핀1
const int echoPin = 12;  // Echo 핀1
const int trigPin2 = 11;  // Trigger 핀2
const int echoPin2 = 10;  // Echo 핀2
const int abPin = 4;      // A/B 핀에 연결
const int pin1A = 5;      // 1A 핀에 연결
const int pin2A = 6;      // 2A 핀에 연결
const int pin1B = 7;      // 1B 및 2B 핀에 연결
unsigned long previousMillis = 0;
unsigned long buzzerInterval = 0;
const int stableRange = 20;

// 로우 패스 필터 셋팅
float filteredDistance1 = 0.0;
float filteredDistance2 = 0.0;
float alpha = 0.5; // 필터 계수, 0과 1 사이의 값으로 설정. 작을수록 필터링 효과가 큼

void setup() {
  Serial.begin(9600);  // 시리얼 통신 시작
  pinMode(trigPin, OUTPUT);
  pinMode(echoPin, INPUT);
  pinMode(trigPin2, OUTPUT);
  pinMode(echoPin2, INPUT);
  pinMode(abPin, OUTPUT);
  pinMode(pin1A, OUTPUT);
  pinMode(pin2A, OUTPUT);
  pinMode(pin1B, OUTPUT);
}

void loop() {
  // 거리 측정
  long distance1 = getDistance(trigPin, echoPin);
  long distance2 = getDistance(trigPin2, echoPin2);

  // 로우 패스 필터 적용
  filteredDistance1 = applyLowPassFilter(distance1, filteredDistance1);
  filteredDistance2 = applyLowPassFilter(distance2, filteredDistance2);

  // 두 센서 간 거리 값 중 가장 작은 값을 기준으로 장애물 위협을 판단
  long distance = min(filteredDistance1, filteredDistance2);
  long distanceDifference = abs(filteredDistance1 - filteredDistance2);

  // 거리 값에 따라 사용할 스피커 결정
  int buzzer = selectBuzzer(distanceDifference);

  // 거리 값이 400cm 이상 2cm 이하일 때는 감지하지 않음
  if (distance > 25) {
    handleBuzzer(distance, buzzer);
  }

  delay(80);
}

// 초음파를 이용해 거리 측정 함수
long getDistance(int trigPin, int echoPin) {
  long duration;
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(20);
  digitalWrite(trigPin, LOW);
  duration = pulseIn(echoPin, HIGH);
  return duration * 0.034 / 2;
}

// 로우 패스 필터 적용 함수
float applyLowPassFilter(long distance, float filteredDistance) {
  return alpha * distance + (1 - alpha) * filteredDistance;
}

// 스피커를 선택하는 함수
int selectBuzzer(long distanceDifference) {
  int buzzer = 0;

  if (distanceDifference <= stableRange) {
    buzzer = pin1B;
    digitalWrite(abPin, HIGH); // A/B 핀 HIGH (둘 다 활성화)
  } else if (filteredDistance1 < filteredDistance2) {
    buzzer = pin1A;
    digitalWrite(abPin, LOW); // A/B 핀 LOW (1번 스피커만 활성화)
  } else {
    buzzer = pin2A;
    digitalWrite(abPin, LOW); // A/B 핀 LOW (2번 스피커만 활성화)
  }

  return buzzer;
}

// 거리 값에 따라 부저 처리 함수
void handleBuzzer(long distance, int buzzer) {
  unsigned long currentMillis = millis();
  
  if (distance > 150) {
    noTone(buzzer);
    buzzerInterval = 0;
  } else if (distance > 110) {
    beepBuzzer(buzzer, 50, 400);
  } else if (distance > 80) {
    beepBuzzer(buzzer, 50, 300);
  } else if (distance > 50) {
    beepBuzzer(buzzer, 50, 100);
  } else {
    beepBuzzer(buzzer, 1000, 10);
  }
}

// 부저 소리를 지정된 간격으로 발생시키는 함수
void beepBuzzer(int buzzer, int toneDuration, int interval) {
  unsigned long currentMillis = millis();
  if (currentMillis - previousMillis >= buzzerInterval) {
    previousMillis = currentMillis;
    tone(buzzer, 900, toneDuration);
    buzzerInterval = interval;
  }
}