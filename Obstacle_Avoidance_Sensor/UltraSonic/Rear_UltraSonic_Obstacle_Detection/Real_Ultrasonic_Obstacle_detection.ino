const int trigPin = 13;  // Trigger 핀1
const int echoPin = 12;  // Echo 핀1
const int trigPin2 = 11;  // Trigger 핀2X
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
  pinMode(trigPin, OUTPUT);  // Trigger1 핀을 출력으로 설정
  pinMode(echoPin, INPUT);   // Echo1 핀을 입력으로 설정
  pinMode(trigPin2, OUTPUT);  // Trigger2 핀을 출력으로 설정
  pinMode(echoPin2, INPUT);   // Echo2 핀을 입력으로 설정
  pinMode(abPin, OUTPUT);
  pinMode(pin1A, OUTPUT);
  pinMode(pin2A, OUTPUT);
  pinMode(pin1B, OUTPUT);
}

void loop() {
  long duration1, distance1;
  long duration2, distance2;
  long distance;
  int buzzer = 0;

  // 1번 센서 초음파 신호 보내기 (JSN_B02는 20us)
  digitalWrite(trigPin, LOW);  
  delayMicroseconds(2); 
  digitalWrite(trigPin, HIGH); 
  delayMicroseconds(20); 
  digitalWrite(trigPin, LOW); 
  
  // 1번 초음파 반사 시간 측정
  duration1 = pulseIn(echoPin, HIGH);
  delay(10);
  // 2번 센서 초음파 신호 보내기
  digitalWrite(trigPin2, LOW);  
  delayMicroseconds(2); 
  digitalWrite(trigPin2, HIGH); 
  delayMicroseconds(20); 
  digitalWrite(trigPin2, LOW); 
  
  // 2번 초음파 반사 시간 측정
  duration2 = pulseIn(echoPin2, HIGH);

  // 거리 계산 (cm 단위)
  distance1 = duration1 * 0.034 / 2;
  distance2 = duration2 * 0.034 / 2;

  // 로우 패스 필터 적용
  filteredDistance1 = alpha * distance1 + (1 - alpha) * filteredDistance1;
  filteredDistance2 = alpha * distance2 + (1 - alpha) * filteredDistance2;

  // 두 센서 간 거리 값 중 가장 작은 값을 기준으로 장애물 위협을 판단
  distance = min(filteredDistance1, filteredDistance2);
  long distanceDifference = abs(filteredDistance1 - filteredDistance2);
  

  // 거리 값에 따라 사용할 스피커 결정
  if (distanceDifference <= stableRange) {
    buzzer = pin1B;  // 둘 다 출력하려면 1B 핀을 사용
    digitalWrite(abPin, HIGH); // A/B 핀 HIGH (둘 다 활성화)
  } else if (filteredDistance1 < filteredDistance2) {
    buzzer = pin1A;
    digitalWrite(abPin, LOW); // A/B 핀 LOW (1번 스피커만 활성화)
  } else {
    buzzer = pin2A;
    digitalWrite(abPin, LOW); // A/B 핀 LOW (2번 스피커만 활성화)
  }

  // 시리얼 플로터에 출력할 데이터
  Serial.println(distance); // 가장 작은 거리 (필터 적용됨)

  // 거리 값이 400cm 이상 2cm 이하일 때는 감지하지 않음
  if (distance > 25) {
    unsigned long currentMillis = millis();
    if (distance > 150) {
      noTone(buzzer);
      buzzerInterval = 0;
    } else if (distance > 110) {
      if (currentMillis - previousMillis >= buzzerInterval) {
        previousMillis = currentMillis;
        tone(buzzer, 900, 50);
        buzzerInterval = 400;  // 400ms 간격으로 소리 발생
      }
    } else if (distance > 80) {
      if (currentMillis - previousMillis >= buzzerInterval) {
        previousMillis = currentMillis;
        tone(buzzer, 900, 50);
        buzzerInterval = 300;  // 300ms 간격으로 소리 발생
      }
    } else if (distance > 50) {
      if (currentMillis - previousMillis >= buzzerInterval) {
        previousMillis = currentMillis;
        tone(buzzer, 900, 50);
        buzzerInterval = 100;  // 100ms 간격으로 소리 발생
      }
    } else {
      if (currentMillis - previousMillis >= buzzerInterval) {
        previousMillis = currentMillis;
        tone(buzzer, 900, 1000);
        buzzerInterval = 10;  // 10ms 간격으로 소리 발생
      }
    }
  }

  delay(80);
}
