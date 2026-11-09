import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile_app/config/twin_address.dart';
import 'package:mobile_app/utils/digital_twin_api.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    TwinAddress.debugSet(null);
  });

  test('normalize accepts a bare IP and adds http and port 8080', () {
    expect(TwinAddress.normalize('192.168.1.5'), 'http://192.168.1.5:8080');
    expect(TwinAddress.normalize(' 10.0.0.2:9000 '), 'http://10.0.0.2:9000');
    expect(TwinAddress.normalize('http://h:8080/'), 'http://h:8080');
    expect(
      TwinAddress.normalize('https://twin.example'),
      'https://twin.example',
    );
    expect(TwinAddress.normalize(''), isNull);
    expect(TwinAddress.normalize('ftp://h'), isNull);
  });

  test('save persists, load restores, empty clears', () async {
    await TwinAddress.save('172.20.10.4');
    expect(TwinAddress.current, 'http://172.20.10.4:8080');
    TwinAddress.debugSet(null);
    await TwinAddress.load();
    expect(TwinAddress.override, 'http://172.20.10.4:8080');
    await TwinAddress.save('');
    expect(TwinAddress.override, isNull);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString(TwinAddress.prefsKey), isNull);
  });

  test('save rejects an unusable address', () async {
    expect(() => TwinAddress.save('ftp://x'), throwsFormatException);
  });

  test('requests follow an address changed after the api was built', () async {
    final hits = <Uri>[];
    final api = DigitalTwinApi(
      client: MockClient((r) async {
        hits.add(r.url);
        return http.Response('{}', 200);
      }),
    );
    TwinAddress.debugSet('http://10.0.0.1:8080');
    await api.request('GET', '/v1/x');
    TwinAddress.debugSet('http://10.0.0.2:8080');
    await api.request('GET', '/v1/x');
    expect(hits.map((u) => u.host), ['10.0.0.1', '10.0.0.2']);
  });
}
